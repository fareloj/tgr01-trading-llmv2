import math
import time

# get_connection removed


class RiskManager:
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

    def calculate_system_reliability(self, payload: dict) -> float:
        """
        Calcula o penalizador de confianca baseado na saude dos dados em tempo real.
        Retorna um valor entre 0.0 e 1.0.
        """
        reliability = 1.0

        news = payload.get("news_context", [])
        if len(news) == 0:
            print("[Risk] Aviso: Sem noticias recentes. Penalizando confiabilidade estrutural (x0.7).")
            reliability *= 0.7

        data_health = payload.get("data_health", {})
        if data_health.get("is_market_data_stale"):
            print("[Risk] Aviso: Market data stale. Penalizando confiabilidade estrutural (x0.3).")
            reliability *= 0.3

        if data_health.get("is_news_stale"):
            print("[Risk] Aviso: Noticias stale. Penalizando confiabilidade estrutural (x0.6).")
            reliability *= 0.6

        news_risk = payload.get("news_risk", {})
        if news_risk.get("has_negative_red_flag"):
            print("[Risk] Aviso: Red flag negativa em noticias. Penalizando confiabilidade estrutural (x0.7).")
            reliability *= 0.7

        tech = payload.get("technical_context", {})
        atr = self._atr_value(tech)
        try:
            current_price = float(tech.get("current_price", 0.0))
        except (TypeError, ValueError, OverflowError):
            return 0.0

        if not math.isfinite(current_price) or current_price <= 0:
            return 0.0
        if not math.isfinite(atr) or atr < 0:
            return 0.0

        if current_price > 0 and (atr / current_price) > 0.05:
            print("[Risk] Aviso: Volatilidade extrema detectada. Penalizando (x0.5).")
            reliability *= 0.5

        return reliability

    def calculate_fractional_kelly(self, win_rate: float, risk_reward_ratio: float, fraction: float = 0.5) -> float:
        """
        Calcula o Kelly Fracionado para definir o tamanho seguro da aposta.
        Retorna a porcentagem da banca que deve ser alocada na ordem.
        """
        try:
            win_rate = float(win_rate)
            risk_reward_ratio = float(risk_reward_ratio)
            fraction = float(fraction)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if not all(math.isfinite(value) for value in (win_rate, risk_reward_ratio, fraction)):
            return 0.0
        if not 0 < win_rate < 1 or risk_reward_ratio <= 0 or not 0 < fraction <= 1:
            return 0.0

        kelly_perc = win_rate - ((1 - win_rate) / risk_reward_ratio)

        if kelly_perc <= 0:
            return 0.0

        return kelly_perc * fraction * 100.0

    def evaluate_order(self, llm_action: str, llm_conviction: int, payload: dict, current_exposure: float) -> dict:
        """
        A muralha deterministica: onde o LLM e barrado pela matematica e pela saude dos dados.
        """
        action = str(llm_action).strip().upper()

        try:
            conviction = float(llm_conviction)
            exposure = float(current_exposure)
            max_allowed = float(
                payload.get("portfolio_context", {}).get("max_allowed_risk_per_trade", 5.0)
            )
        except (TypeError, ValueError, OverflowError):
            return self._hold("Risk input invalido ou nao numerico.")
        if not all(math.isfinite(value) for value in (conviction, exposure, max_allowed)):
            return self._hold("Risk input nao finito.")
        if not 0 <= conviction <= 100:
            return self._hold("Conviccao fora do intervalo 0..100.")
        if exposure < 0:
            return self._hold("Exposicao nao pode ser negativa.")
        if not 0 < max_allowed <= 100:
            return self._hold("Limite por trade fora do intervalo 0..100.")

        if action == "HOLD":
            return {"action": "HOLD", "reason": "LLM sugeriu HOLD.", "executed_size": 0.0}

        if action not in {"BUY", "SELL"}:
            return {"action": "HOLD", "reason": f"LLM sugeriu acao invalida: {llm_action}", "executed_size": 0.0}

        directional_block = self._directional_gate(action, payload)
        if directional_block:
            return directional_block

        cooldown_block = self._cooldown_gate(action)
        if cooldown_block:
            return cooldown_block

        if conviction < 70:
            return {
                "action": "HOLD",
                "reason": f"Conviccao bruta da IA insuficiente ({conviction:g}%). Exige-se minimo de 70%.",
                "executed_size": 0.0,
            }

        news = payload.get("news_context", [])
        if len(news) == 0 and conviction < 80:
            return {
                "action": "HOLD",
                "reason": f"Noticias velhas/ausentes. IA nao tem conviccao absoluta ({conviction:g}% < 80%).",
                "executed_size": 0.0,
            }

        sys_rel = self.calculate_system_reliability(payload)
        hybrid_confidence = (conviction / 100.0) * sys_rel

        if hybrid_confidence < 0.50:
            return {
                "action": "HOLD",
                "reason": f"Confianca Hibrida muito baixa ({hybrid_confidence * 100:.1f}%). Limiar e 50%.",
                "executed_size": 0.0,
            }

        if action == "BUY" and current_exposure >= self.max_exposure:
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
            executed_size = min(max_allowed, current_exposure)
            if executed_size <= 0:
                return {"action": "HOLD", "reason": "SELL bloqueado: portfolio sem exposicao em BTC.", "executed_size": 0.0}

            size_label = f"Reducao de exposicao: {executed_size:.2f}%"

        return {
            "action": action,
            "reason": f"Aprovado. Confianca Hibrida: {hybrid_confidence * 100:.1f}%. {size_label}",
            "executed_size": executed_size,
        }

    def _directional_gate(self, action: str, payload: dict) -> dict | None:
        data_health = payload.get("data_health", {})
        tech = payload.get("technical_context", {})
        rsi_status = tech.get("rsi", {}).get("status")
        macd_status = tech.get("macd", {}).get("status")
        atr_status = self._atr_status(tech)

        if data_health.get("is_market_data_stale"):
            return self._hold(f"Directional Gate: {action} bloqueado por market data stale")

        news_risk = payload.get("news_risk", {})
        if news_risk.get("has_untrusted_instruction"):
            return self._hold(f"Directional Gate: {action} bloqueado por instrucao nao confiavel em noticias")

        if action == "BUY":
            if data_health.get("is_news_stale"):
                return self._hold("Directional Gate: BUY bloqueado por noticias stale")
            if news_risk.get("has_negative_red_flag"):
                terms = ", ".join(news_risk.get("matched_terms", [])) or "unknown"
                return self._hold(f"Directional Gate: BUY bloqueado por news red flag ({terms})")
            if rsi_status == "OVERBOUGHT":
                return self._hold("Directional Gate: BUY bloqueado por RSI OVERBOUGHT")
            if rsi_status == "OVERSOLD" and macd_status not in {"BULLISH_EXPANDING", "BULLISH_DIVERGENCE"}:
                return self._hold(
                    "Directional Gate: BUY bloqueado por RSI OVERSOLD sem confirmacao MACD bullish"
                )
            if macd_status in {"BEARISH_EXPANDING", "BEARISH_DIVERGENCE"}:
                return self._hold(f"Directional Gate: BUY bloqueado por MACD {macd_status}")
            if atr_status == "EXTREME":
                return self._hold("Directional Gate: BUY bloqueado por ATR EXTREME")

        if action == "SELL":
            if rsi_status == "OVERSOLD":
                return self._hold("Directional Gate: SELL bloqueado por RSI OVERSOLD")
            if macd_status in {"BULLISH_EXPANDING", "BULLISH_DIVERGENCE"}:
                return self._hold(f"Directional Gate: SELL bloqueado por MACD {macd_status}")

        return None

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

    def _atr_value(self, tech: dict) -> float:
        atr = tech.get("volatility_atr", 0.0)
        try:
            if isinstance(atr, dict):
                return float(atr.get("value", 0.0) or 0.0)
            return float(atr or 0.0)
        except (TypeError, ValueError, OverflowError):
            return math.nan

    def _atr_status(self, tech: dict) -> str | None:
        atr = tech.get("volatility_atr")
        if isinstance(atr, dict):
            return atr.get("status")
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
