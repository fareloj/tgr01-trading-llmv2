import decimal
import math
import numbers
import time

# get_connection removed

# A directional gate can only reason about RSI/MACD when the status is a known
# label. Anything else -- a missing key, None, a non-string, or an explicit
# "UNKNOWN" -- is absent evidence, and absent evidence must not pass a gate.
# `UNKNOWN` is deliberately excluded even though the audit memory allows it as a
# stored value: a record may be unknown, a gate decision may not be.
KNOWN_RSI_STATUS = frozenset({"OVERSOLD", "OVERBOUGHT", "NEUTRAL"})
KNOWN_MACD_STATUS = frozenset(
    {
        "BULLISH_EXPANDING",
        "BEARISH_EXPANDING",
        "BULLISH_DIVERGENCE",
        "BEARISH_DIVERGENCE",
        "NEUTRAL",
    }
)
# An ATR above this fraction of price is extreme. Shared by the reliability
# penalty and the directional gate so the two can never disagree about the same
# payload.
EXTREME_ATR_RATIO = 0.05


def usable_news_count(news: object) -> int:
    """Count `news_context` entries that are actually news records.

    A list of `None`/ints/strings has a length and is not empty, so `len(news)`
    read it as "news present" while carrying no headline to corroborate a
    directional proposal. Only a mapping with a non-blank headline counts.

    Exact builtin types only: a `list`/`dict` subclass can override `__iter__`
    or `get` and raise, and an exception here would abort the cycle instead of
    producing the audited HOLD. The check is wrapped as well, because iterating
    a subclass that passes the type test is not guaranteed to be safe.
    """
    if type(news) is not list:
        return 0
    count = 0
    try:
        for item in news:
            if type(item) is not dict:
                continue
            headline = item.get("headline")
            if isinstance(headline, str) and headline.strip():
                count += 1
    except Exception:
        return 0
    return count


def safe_float(value, *, allow_numeric_string: bool = True) -> float | None:
    """Coerce a numeric payload value to a finite float, or None when unusable.

    `float()` runs arbitrary code: a subclass with a hostile `__float__` raises
    something the callers' `except (TypeError, ValueError, OverflowError)` does
    not catch, and the exception aborts the cycle instead of producing the
    audited HOLD. Only real numeric types and, when allowed, non-blank numeric
    strings are accepted; everything else becomes None, which each caller turns
    into its own fail-closed reason.

    `allow_numeric_string=False` is used for the risk inputs (conviction,
    exposure, sizing, drawdown). Those must be numbers: HEAD raised `TypeError`
    on a string, and accepting one would turn a crash into an approval, which is
    a relaxation. The live pipeline always passes numbers there.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not allow_numeric_string:
            return None
        try:
            if value.strip() == "":
                return None
        except Exception:
            return None
    elif not isinstance(value, (numbers.Real, decimal.Decimal)):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


class RiskManager:
    # Conviction the model must reach for a directional proposal to be
    # executable. Named because the LLM prompt is calibrated against it (see
    # decision_agent.evaluate_market) and the operator console displays it; the
    # three must not drift apart.
    MINIMUM_CONVICTION = 70
    # When no news context is present, the bar is higher: the model must be more
    # certain to act without news corroboration.
    NO_NEWS_MINIMUM_CONVICTION = 80
    # Hybrid confidence floor: (conviction / 100) * system_reliability. The floor
    # is EXCLUSIVE: a proposal exactly at the floor does not carry the margin the
    # gate requires, so it is held. This was an inclusive comparison until
    # 2026-09-17, which let conviction 100 with a 0.5 reliability penalty approve
    # at exactly 0.50.
    MINIMUM_HYBRID_CONFIDENCE = 0.50

    def __init__(
        self,
        max_daily_drawdown: float = 10.0,
        max_exposure: float = 100.0,
        cooldown_minutes: int = 15,
    ):
        try:
            drawdown_limit = float(max_daily_drawdown)
            exposure_limit = float(max_exposure)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("risk limits must be numeric") from error
        if not math.isfinite(drawdown_limit) or drawdown_limit <= 0:
            raise ValueError("max_daily_drawdown must be finite and positive")
        if not math.isfinite(exposure_limit) or not 0 < exposure_limit <= 100:
            raise ValueError("max_exposure must be finite and between 0 and 100")
        if isinstance(cooldown_minutes, bool) or not isinstance(cooldown_minutes, int) or cooldown_minutes < 0:
            raise ValueError("cooldown_minutes must be a non-negative integer")
        self.max_daily_drawdown = drawdown_limit
        self.max_exposure = exposure_limit
        self.cooldown_minutes = cooldown_minutes

    def calculate_system_reliability(self, payload: dict, action: str | None = None) -> float:
        """Fail-closed reliability score in [0.0, 1.0]; this method never raises.

        A malformed payload returns 0.0 (maximum penalty) rather than raising.
        A structural field that cannot be read is not evidence of health. The
        exception boundary covers `__eq__`/`__hash__` overrides on a hostile key,
        which a plain `.get()` can still trigger.
        """
        try:
            return self._calculate_system_reliability(payload, action)
        except Exception:
            return 0.0

    def _calculate_system_reliability(self, payload: dict, action: str | None = None) -> float:
        if type(payload) is not dict:
            return 0.0
        reliability = 1.0

        news = payload.get("news_context", [])
        if type(news) is not list:
            return 0.0
        if usable_news_count(news) == 0:
            print("[Risk] Aviso: Sem noticias recentes. Penalizando confiabilidade estrutural (x0.7).")
            reliability *= 0.7

        data_health = self._as_mapping(payload, "data_health")
        if data_health is None:
            return 0.0
        # `{}` is not evidence of freshness, and neither is a flag that is not a
        # boolean. Require the flags the live builder always emits, as booleans.
        # See the same guard in `_directional_gate`.
        if not isinstance(data_health.get("is_market_data_stale"), bool):
            return 0.0
        if not isinstance(data_health.get("is_news_stale"), bool):
            return 0.0
        if data_health.get("is_market_data_stale"):
            print("[Risk] Aviso: Market data stale. Penalizando confiabilidade estrutural (x0.3).")
            reliability *= 0.3

        if data_health.get("is_news_stale"):
            print("[Risk] Aviso: Noticias stale. Penalizando confiabilidade estrutural (x0.6).")
            reliability *= 0.6

        news_risk = self._as_mapping(payload, "news_risk")
        if news_risk is None:
            return 0.0
        if not isinstance(news_risk.get("has_negative_red_flag"), bool):
            return 0.0
        if not isinstance(news_risk.get("has_untrusted_instruction"), bool):
            return 0.0
        if news_risk.get("has_negative_red_flag") and not self._negative_news_confirms_sell(payload, action):
            print("[Risk] Aviso: Red flag negativa em noticias. Penalizando confiabilidade estrutural (x0.7).")
            reliability *= 0.7

        tech = self._as_mapping(payload, "technical_context")
        if tech is None:
            return 0.0
        atr = self._atr_value(tech)
        current_price = safe_float(tech.get("current_price", 0.0))
        if current_price is None or current_price <= 0:
            return 0.0
        if not math.isfinite(atr) or atr < 0:
            return 0.0

        if current_price > 0 and (atr / current_price) > EXTREME_ATR_RATIO:
            print("[Risk] Aviso: Volatilidade extrema detectada. Penalizando (x0.5).")
            reliability *= 0.5

        return reliability

    def calculate_fractional_kelly(self, win_rate: float, risk_reward_ratio: float, fraction: float = 0.5) -> float:
        """
        Calcula o Kelly Fracionado para definir o tamanho seguro da aposta.
        Retorna a porcentagem da banca que deve ser alocada na ordem.
        """
        win_rate = safe_float(win_rate, allow_numeric_string=False)
        risk_reward_ratio = safe_float(risk_reward_ratio, allow_numeric_string=False)
        fraction = safe_float(fraction, allow_numeric_string=False)
        if win_rate is None or risk_reward_ratio is None or fraction is None:
            return 0.0
        if not 0 < win_rate < 1 or risk_reward_ratio <= 0 or not 0 < fraction <= 1:
            return 0.0

        kelly_perc = win_rate - ((1 - win_rate) / risk_reward_ratio)

        if kelly_perc <= 0:
            return 0.0

        return kelly_perc * fraction * 100.0

    def evaluate_order(self, llm_action: str, llm_conviction: int, payload: dict, current_exposure: float) -> dict:
        """Fail-closed entry point: this method never raises.

        A malformed payload must produce a HOLD with an audited reason. Every
        field is coerced defensively, and the whole evaluation runs inside an
        exception boundary as a last resort: an in-process object can override
        `__eq__`/`__hash__`/`__getattr__`, so a shape nobody anticipated must
        still produce a decision rather than aborting an otherwise auditable
        cycle. Until 2026-09-17 several shapes raised here, and a crash is not an
        audited safety decision.
        """
        try:
            return self._evaluate_order(llm_action, llm_conviction, payload, current_exposure)
        except Exception:
            return self._hold("Risk Manager nao conseguiu avaliar o payload (malformado).")

    def _evaluate_order(self, llm_action: str, llm_conviction: int, payload: dict, current_exposure: float) -> dict:
        # `str()` runs arbitrary code: a `str` subclass whose `__str__` raises
        # would abort the cycle. The action is Pydantic-validated upstream, so
        # this is belt and braces, but it keeps the "never raise" guarantee.
        try:
            action = str(llm_action).strip().upper()
        except Exception:
            return self._hold("Acao da IA ilegivel (payload malformado).")

        portfolio = self._as_mapping(payload, "portfolio_context")
        if portfolio is None:
            return self._hold("portfolio_context ausente ou malformado.")

        conviction = safe_float(llm_conviction, allow_numeric_string=False)
        exposure = safe_float(current_exposure, allow_numeric_string=False)
        if conviction is None or exposure is None:
            return self._hold("Risk input invalido, nao numerico ou nao finito.")
        # M2: no default. An absent or non-numeric sizing limit is absent safety
        # evidence; defaulting to 5.0 approved on a value the payload never
        # carried. Every producer emits this key.
        if "max_allowed_risk_per_trade" not in portfolio:
            return self._hold("Limite por trade ausente no portfolio_context.")
        max_allowed = safe_float(
            portfolio.get("max_allowed_risk_per_trade"), allow_numeric_string=False
        )
        if max_allowed is None:
            return self._hold("Limite por trade invalido, nao numerico ou nao finito.")
        if not 0 <= conviction <= 100:
            return self._hold("Conviccao fora do intervalo 0..100.")
        if exposure < 0:
            return self._hold("Exposicao nao pode ser negativa.")
        if not 0 < max_allowed <= 100:
            return self._hold("Limite por trade fora do intervalo 0..100.")

        if action == "HOLD":
            return {"action": "HOLD", "reason": "LLM sugeriu HOLD.", "executed_size": 0.0}

        if action not in {"BUY", "SELL"}:
            # `action` is the already-sanitized string. Interpolating the raw
            # argument could run a hostile `__format__` and abort the cycle.
            return {"action": "HOLD", "reason": f"LLM sugeriu acao invalida: {action}", "executed_size": 0.0}

        if action == "BUY":
            drawdown = portfolio.get("daily_drawdown_percentage")
            if drawdown is not None:
                drawdown = safe_float(drawdown, allow_numeric_string=False)
                if drawdown is None or drawdown < 0:
                    return self._hold("Drawdown diario invalido, nao numerico ou nao finito.")
                if drawdown >= self.max_daily_drawdown:
                    return self._hold(
                        "Limite de drawdown diario atingido "
                        f"({drawdown:.2f}% >= {self.max_daily_drawdown:.2f}%). BUY bloqueado."
                    )

        directional_block = self._directional_gate(action, payload)
        if directional_block:
            return directional_block

        cooldown_block = self._cooldown_gate(action)
        if cooldown_block:
            return cooldown_block

        if conviction < self.MINIMUM_CONVICTION:
            return {
                "action": "HOLD",
                "reason": (
                    f"Conviccao bruta da IA insuficiente ({conviction:g}%). "
                    f"Exige-se minimo de {self.MINIMUM_CONVICTION}%."
                ),
                "executed_size": 0.0,
            }

        news = payload.get("news_context", [])
        # A truthy non-list (a string, a dict) has a length and is not empty, so
        # it silently read as "news present" while carrying no usable records.
        if type(news) is not list:
            return self._hold("news_context presente mas nao e lista (payload malformado).")
        if usable_news_count(news) == 0 and conviction < self.NO_NEWS_MINIMUM_CONVICTION:
            return {
                "action": "HOLD",
                "reason": (
                    f"Noticias velhas/ausentes. IA nao tem conviccao absoluta "
                    f"({conviction:g}% < {self.NO_NEWS_MINIMUM_CONVICTION}%)."
                ),
                "executed_size": 0.0,
            }

        sys_rel = self.calculate_system_reliability(payload, action=action)
        hybrid_confidence = (conviction / 100.0) * sys_rel

        # Exclusive floor: a proposal exactly at the floor has no margin above
        # it, so it is held. `<=` is the fix for the inclusive-boundary finding.
        if hybrid_confidence <= self.MINIMUM_HYBRID_CONFIDENCE:
            return {
                "action": "HOLD",
                "reason": (
                    f"Confianca Hibrida insuficiente ({hybrid_confidence * 100:.1f}%). "
                    f"Exige-se mais de {self.MINIMUM_HYBRID_CONFIDENCE * 100:.0f}%."
                ),
                "executed_size": 0.0,
            }

        # Use the sanitized `exposure`, not the raw argument: `safe_float` accepts
        # a numeric string, which the raw value's comparison would raise on.
        if action == "BUY" and exposure >= self.max_exposure:
            return {
                "action": "HOLD",
                "reason": f"Teto de alocacao de portfolio ({self.max_exposure}%) atingido. Compras bloqueadas.",
                "executed_size": 0.0,
            }

        executed_size = 0.0
        if action == "BUY":
            raw_size = self.calculate_fractional_kelly(win_rate=0.55, risk_reward_ratio=1.5, fraction=0.5)
            executed_size = min(raw_size, max_allowed)

            if executed_size <= 0:
                return {"action": "HOLD", "reason": "Matematica de Kelly sugere lote nulo ou negativo.", "executed_size": 0.0}

            size_label = f"Tamanho do Kelly: {executed_size:.2f}%"

        if action == "SELL":
            executed_size = min(max_allowed, exposure)
            if executed_size <= 0:
                return {"action": "HOLD", "reason": "SELL bloqueado: portfolio sem exposicao em BTC.", "executed_size": 0.0}

            size_label = f"Reducao de exposicao: {executed_size:.2f}%"

        return {
            "action": action,
            "reason": f"Aprovado. Confianca Hibrida: {hybrid_confidence * 100:.1f}%. {size_label}",
            "executed_size": executed_size,
        }

    def _directional_gate(self, action: str, payload: dict) -> dict | None:
        data_health = self._as_mapping(payload, "data_health")
        news_risk = self._as_mapping(payload, "news_risk")
        tech = self._as_mapping(payload, "technical_context")

        # An EMPTY mapping is the same defect class as an absent one: `{}` made
        # `is_market_data_stale` falsy ("fresh") and `has_untrusted_instruction`
        # falsy ("no injection"), so the gate approved on health evidence it never
        # received. The live builder always populates these flags; a payload that
        # omits them is not evidence of health.
        #
        # Presence alone is not enough: a flag present with a falsy non-bool
        # (`None`, `0`, `""`) also read as "fresh"/"no injection". A boolean flag
        # must BE a boolean, matching the "UNKNOWN is not a gate value" rule
        # applied to RSI/MACD.
        #
        # This type check runs BEFORE the hazard checks. Reporting a type defect
        # as "market data stale" would misdescribe the trigger: `1` and the string
        # "false" are not observations of staleness, they are malformed payloads.
        if data_health is not None:
            bad_health = [
                key
                for key in ("is_market_data_stale", "is_news_stale")
                if not isinstance(data_health.get(key), bool)
            ]
            if bad_health:
                return self._hold(
                    "Directional Gate: "
                    f"{action} bloqueado por data_health sem booleano em {', '.join(bad_health)}"
                )
        if news_risk is not None:
            bad_risk = [
                key
                for key in ("has_negative_red_flag", "has_untrusted_instruction")
                if not isinstance(news_risk.get(key), bool)
            ]
            if bad_risk:
                return self._hold(
                    "Directional Gate: "
                    f"{action} bloqueado por news_risk sem booleano em {', '.join(bad_risk)}"
                )

        # A KNOWN hazard wins the reason string over a missing sibling field: if
        # stale market data is reported, that is the more actionable fact.
        if data_health is not None and data_health.get("is_market_data_stale"):
            return self._hold(f"Directional Gate: {action} bloqueado por market data stale")

        if news_risk is not None and news_risk.get("has_untrusted_instruction"):
            return self._hold(f"Directional Gate: {action} bloqueado por instrucao nao confiavel em noticias")

        if action == "BUY":
            if data_health is not None and data_health.get("is_news_stale"):
                return self._hold("Directional Gate: BUY bloqueado por noticias stale")
            if news_risk is not None and news_risk.get("has_negative_red_flag"):
                matched = news_risk.get("matched_terms")
                if type(matched) is not list:
                    matched = []
                # `str(term)` runs arbitrary code on a hostile object, so build
                # the reason defensively rather than aborting on a bad item.
                labels = []
                for term in matched:
                    try:
                        labels.append(str(term))
                    except Exception:
                        labels.append("<unprintable>")
                terms = ", ".join(labels) or "unknown"
                return self._hold(f"Directional Gate: BUY bloqueado por news red flag ({terms})")

        if data_health is None:
            return self._hold(
                f"Directional Gate: {action} bloqueado por data_health ausente ou malformado"
            )
        if news_risk is None:
            return self._hold(
                f"Directional Gate: {action} bloqueado por news_risk ausente ou malformado"
            )
        if tech is None:
            return self._hold(
                f"Directional Gate: {action} bloqueado por technical_context ausente ou malformado"
            )

        rsi_status = self._status_of(tech, "rsi")
        macd_status = self._status_of(tech, "macd")
        atr_status = self._atr_status(tech)
        atr_ratio_extreme = self._atr_is_extreme(tech)

        # Absent volatility evidence must not pass a directional gate either.
        atr_block = self._atr_evidence_block(action, tech)
        if atr_block:
            return atr_block

        # Absent indicator evidence must not pass a directional gate. Until
        # 2026-09-17 a missing `rsi`/`macd` key produced None, which matched no
        # blocklist, so the gate approved on evidence it never received.
        # `_status_of` returns a plain `str` or None, so the set membership below
        # cannot raise on an unhashable or hostile status.
        if rsi_status not in KNOWN_RSI_STATUS:
            return self._hold(
                f"Directional Gate: {action} bloqueado por RSI ausente ou desconhecido ({rsi_status!r})"
            )
        if macd_status not in KNOWN_MACD_STATUS:
            return self._hold(
                f"Directional Gate: {action} bloqueado por MACD ausente ou desconhecido ({macd_status!r})"
            )

        if action == "BUY":
            if rsi_status == "OVERBOUGHT":
                return self._hold("Directional Gate: BUY bloqueado por RSI OVERBOUGHT")
            if rsi_status == "OVERSOLD" and macd_status not in {"BULLISH_EXPANDING", "BULLISH_DIVERGENCE"}:
                return self._hold(
                    "Directional Gate: BUY bloqueado por RSI OVERSOLD sem confirmacao MACD bullish"
                )
            if macd_status in {"BEARISH_EXPANDING", "BEARISH_DIVERGENCE"}:
                return self._hold(f"Directional Gate: BUY bloqueado por MACD {macd_status}")
            # Block on the status OR the ratio. The two used to disagree: a
            # scalar ATR (or a dict with a stale status) skipped this check while
            # the reliability penalty still applied, so a BUY could reach the
            # confidence floor carrying unacknowledged extreme volatility.
            if atr_status == "EXTREME" or atr_ratio_extreme:
                return self._hold("Directional Gate: BUY bloqueado por ATR EXTREME")

        if action == "SELL":
            # No ATR block here on purpose: reducing exposure should not be
            # forbidden by the volatility that makes reducing it attractive.
            # In practice an extreme ratio still ends in HOLD, because it caps
            # `sys_rel` at 0.5 and the exclusive confidence floor then rejects
            # every conviction. That is documented in `_atr_evidence_block`.
            if rsi_status == "OVERSOLD":
                return self._hold("Directional Gate: SELL bloqueado por RSI OVERSOLD")
            if macd_status in {"BULLISH_EXPANDING", "BULLISH_DIVERGENCE"}:
                return self._hold(f"Directional Gate: SELL bloqueado por MACD {macd_status}")

        return None

    def _negative_news_confirms_sell(self, payload: dict, action: str | None) -> bool:
        try:
            normalized_action = str(action or "").strip().upper()
        except Exception:
            return False
        if normalized_action != "SELL":
            return False

        data_health = self._as_mapping(payload, "data_health")
        news_risk = self._as_mapping(payload, "news_risk")
        tech = self._as_mapping(payload, "technical_context")
        if data_health is None or news_risk is None or tech is None:
            return False
        if data_health.get("is_market_data_stale") or data_health.get("is_news_stale"):
            return False
        if news_risk.get("has_untrusted_instruction"):
            return False

        rsi_status = self._status_of(tech, "rsi")
        macd_status = self._status_of(tech, "macd")
        return rsi_status != "OVERSOLD" and macd_status in {
            "BEARISH_EXPANDING",
            "BEARISH_DIVERGENCE",
        }

    def _cooldown_gate(self, action: str) -> dict | None:
        if self.cooldown_minutes <= 0:
            return None

        cutoff = int(time.time()) - (self.cooldown_minutes * 60)
        from backend.core import repository
        last_action_ts = repository.get_last_action_timestamp(action, cutoff)

        if last_action_ts is not None:
            return self._hold(f"Cooldown: {action} repetido nos ultimos {self.cooldown_minutes} minutos")

        return None

    def _hold(self, reason: str) -> dict:
        return {"action": "HOLD", "reason": reason, "executed_size": 0.0}

    @staticmethod
    def _as_mapping(payload: dict, key: str) -> dict | None:
        """Return `payload[key]` as a dict, or None when it is absent/malformed.

        This deliberately does NOT default a missing key to `{}`. An absent
        structural field is absent evidence, and `{}` makes it read as benign:
        a missing `data_health` made `is_market_data_stale` falsy ("fresh"), and
        a missing `news_risk` made `has_untrusted_instruction` falsy ("no
        injection"). Both are the repo's documented defect class -- unknown
        safety data rendered as a safe value -- so absent and malformed are
        treated identically and fail closed.

        `type(..) is dict` rather than `isinstance`: a `dict` subclass can
        override `get` and raise, which would abort the cycle instead of
        returning the audited HOLD this method exists to produce. The audit
        module uses the same exact-type rule.
        """
        if type(payload) is not dict:
            return None
        # `payload.get(key)` runs `__eq__` on every colliding key. A hostile key
        # whose hash matches can raise, so the lookup itself is guarded: an
        # exception reading a field is malformed evidence, not a crash.
        try:
            value = payload.get(key)
        except Exception:
            return None
        return value if type(value) is dict else None

    @staticmethod
    def _status_of(tech: dict, indicator: str) -> object:
        """Read `tech[indicator]["status"]` without raising on a malformed shape.

        Returns a plain `str` or None. Membership tests against
        `KNOWN_RSI_STATUS`/`KNOWN_MACD_STATUS` use a frozenset, and an unhashable
        status (a dict, a list) raises `TypeError` on `in`. A `str` subclass with
        a hostile `__hash__` raises too. Both would abort the gate instead of
        returning HOLD, so anything that is not already a plain string is treated
        as unknown evidence.
        """
        block = tech.get(indicator)
        if type(block) is not dict:
            return None
        status = block.get("status")
        if type(status) is not str:
            return None
        return status

    def _atr_value(self, tech: dict) -> float:
        """Read the ATR number strictly, or NaN when it is not usable.

        `float(atr or 0.0)` treated a falsy bad shape as a calm market:
        `Number("")`, `Number(False)` and `Number([])` are all 0, so `""`,
        `False`, `[]`, `True` (-> 1.0) and `{"value": []}` produced a plausible
        ATR while `{"value": null}` and NaN were rejected. The desktop reader in
        `desktop/src/indicators.mjs` rejects the same shapes, so the backend must
        not be the weaker of the two.

        Accepted: a real numeric type (`int`, `float`, `Decimal`, `Fraction`,
        `numpy` numeric scalars) and a non-blank numeric string. Rejected:
        booleans, `None`, containers, blank or non-numeric strings, and any
        non-finite result. The live pipeline emits `round(float(...))`
        (`backend/features/indicators.py`), so nothing it produces is rejected.

        The type gate is explicit rather than "whatever `float()` accepts". A
        duck-typed object whose `__float__` returns 1.0 is not ATR evidence, and
        `numpy.bool_` is not a `bool` instance so the earlier `isinstance` check
        missed it: `float(np.bool_(True))` is `1.0`, which approved a BUY on
        boolean volatility evidence. `numbers.Real` covers the genuine numeric
        scalars and excludes both.
        """
        raw = tech.get("volatility_atr", 0.0)
        if type(raw) is dict:
            raw = raw.get("value", None)
        value = safe_float(raw)
        return value if value is not None else math.nan

    def _atr_status(self, tech: dict) -> str | None:
        atr = tech.get("volatility_atr")
        if type(atr) is dict:
            status = atr.get("status")
            # Exact `str`: a subclass can define a hostile `__eq__`, and the
            # caller compares this against "EXTREME".
            return status if type(status) is str else None
        return None

    def _atr_is_extreme(self, tech: dict) -> bool:
        """True when the ATR ratio exceeds the extreme threshold, whatever the shape.

        This is the same criterion `calculate_system_reliability` uses for the
        x0.5 penalty. The gate checks it directly so a scalar ATR, or a dict
        whose `status` is stale, cannot slip past the gate while still being
        penalised for the very condition the gate is supposed to block.

        Returns False when the ratio cannot be computed (invalid price or ATR).
        That is "unknown", not "extreme", and saying "ATR EXTREME" for a missing
        price would be a misleading audit reason.

        A payload with an unreadable ATR is still rejected earlier by
        `_atr_evidence_block`. A payload with a readable ATR but a bad price is
        not caught here: it fails closed later, because
        `calculate_system_reliability` returns 0.0 for it and the resulting
        hybrid confidence of 0.0 is held by the floor.
        """
        atr = self._atr_value(tech)
        current_price = safe_float(tech.get("current_price", 0.0))
        if current_price is None or current_price <= 0:
            return False
        if not math.isfinite(atr) or atr < 0:
            return False
        return (atr / current_price) > EXTREME_ATR_RATIO

    def _atr_evidence_block(self, action: str, tech: dict) -> dict | None:
        """Block a directional action when volatility evidence is absent.

        A missing `volatility_atr` key silently became `0.0`, which reads as
        "perfectly calm market" and passes every check. That is the same defect
        class as a missing RSI/MACD status: absent safety data must not look
        benign. A genuine zero is allowed; absent, null, or unreadable is not.

        This blocks BUY and SELL alike, and the asymmetry with the `EXTREME`
        check is deliberate:

        - *Absent* volatility evidence blocks both directions, because the
          execution engine needs a finite ATR for slippage and would otherwise
          fail after the gate had approved; a HOLD is the safe outcome.
        - *Extreme* volatility blocks BUY only. No `SELL`-specific ATR block is
          added, so nothing here is what holds a SELL.

        Measured consequence, recorded because it is easy to misread as
        "SELL stays available in a crash": extreme volatility forces
        `sys_rel <= 0.5`, so the hybrid confidence cannot exceed `0.50` at any
        conviction, and the exclusive floor therefore holds every SELL too.
        A position is **not** reducible through this Risk Manager while the ATR
        ratio is above `EXTREME_ATR_RATIO`. That is the fail-closed outcome, and
        it is a deliberate property of the exclusive floor rather than an
        accident; see `docs/reports/PROJECT_CLOSURE_2026-09-17.md`.
        """
        if "volatility_atr" not in tech:
            return self._hold(
                f"Directional Gate: {action} bloqueado por ATR ausente no technical_context"
            )
        raw = tech.get("volatility_atr")
        if type(raw) is dict and "value" not in raw:
            return self._hold(
                f"Directional Gate: {action} bloqueado por ATR sem valor no technical_context"
            )
        if raw is None or (type(raw) is dict and raw.get("value") is None):
            return self._hold(
                f"Directional Gate: {action} bloqueado por ATR nulo no technical_context"
            )
        atr = self._atr_value(tech)
        if not math.isfinite(atr) or atr < 0:
            return self._hold(
                f"Directional Gate: {action} bloqueado por ATR invalido ou nao finito"
            )
        return None


if __name__ == "__main__":
    print("Testando Risk Manager e Confianca Hibrida...\n")

    mock_payload_ok = {
        "technical_context": {
            "current_price": 50000,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BULLISH_EXPANDING"},
            "volatility_atr": 1000,
        },
        "news_context": [{"headline": "Noticia qualquer valendo 1"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    res1 = rm.evaluate_order("BUY", 90, mock_payload_ok, current_exposure=30.0)
    print("Teste 1 (Tudo Perfeito):", res1)

    res2 = rm.evaluate_order("BUY", 95, mock_payload_ok, current_exposure=85.0)
    print("Teste 2 (Banca Cheia):", res2)

    mock_payload_empty = mock_payload_ok.copy()
    mock_payload_empty["news_context"] = []
    res3 = rm.evaluate_order("BUY", 60, mock_payload_empty, current_exposure=30.0)
    print("Teste 3 (LLM Incerto + Sem Noticia):", res3)
