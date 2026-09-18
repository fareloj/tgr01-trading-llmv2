import math
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR.parent))

from backend.risk.risk_manager import RiskManager


def test_risk_manager_rejects_nonfinite_or_out_of_range_inputs():
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)
    payload = {
        "technical_context": {
            "current_price": 40000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BULLISH_EXPANDING"},
            "volatility_atr": 100.0,
        },
        "news_context": [{"headline": "safe"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    for conviction, exposure in (
        (math.nan, 10.0),
        (math.inf, 10.0),
        (80.0, math.nan),
        (80.0, -1.0),
        (101.0, 10.0),
    ):
        result = rm.evaluate_order("BUY", conviction, payload, exposure)
        assert result["action"] == "HOLD"
        assert result["executed_size"] == 0.0

    payload["portfolio_context"]["max_allowed_risk_per_trade"] = math.nan
    assert rm.evaluate_order("BUY", 80, payload, 10)["action"] == "HOLD"


def test_risk_configuration_and_kelly_reject_invalid_numbers():
    for kwargs in (
        {"max_exposure": math.nan},
        {"max_exposure": 101},
        {"max_daily_drawdown": 0},
        {"cooldown_minutes": -1},
    ):
        try:
            RiskManager(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid configuration accepted: {kwargs}")

    rm = RiskManager()
    assert rm.calculate_fractional_kelly(math.nan, 1.5) == 0.0
    assert rm.calculate_fractional_kelly(1.0, 1.5) == 0.0


def test_daily_drawdown_blocks_buy_but_allows_risk_reducing_sell():
    rm = RiskManager(max_daily_drawdown=5.0, max_exposure=80.0, cooldown_minutes=0)
    payload = {
        "technical_context": {
            "current_price": 40_000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "NEUTRAL"},
            "volatility_atr": 100.0,
        },
        "news_context": [{"headline": "safe"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
        "portfolio_context": {
            "max_allowed_risk_per_trade": 5.0,
            "daily_drawdown_percentage": 5.1,
        },
    }

    blocked = rm.evaluate_order("BUY", 90, payload, current_exposure=20.0)
    assert blocked["action"] == "HOLD"
    assert "drawdown diario" in blocked["reason"]

    allowed = rm.evaluate_order("SELL", 90, payload, current_exposure=20.0)
    assert allowed["action"] == "SELL"
    assert allowed["executed_size"] == 5.0


def test_red_team_flash_crash():
    """
    Scenario 1: test_red_team_flash_crash()
    - Mock a payload with a sudden price drop on the last candle, resulting in extreme volatility
      (ATR value / current_price > 0.05, and/or volatility_atr.status = "EXTREME").
    - Set rsi/macd indicators to positive or neutral signals.
    - Verify that when LLM action is "BUY" (with high conviction, e.g., 90%), the Risk Manager blocks it:
      rm.evaluate_order(...) returns "HOLD" due to "ATR EXTREME" or "Confianca Hibrida".
    """
    # Initialize Risk Manager with 100% max exposure limit and no cooldown to avoid DB dependencies
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)

    # Condition 1: ATR status = "EXTREME"
    payload_extreme_status = {
        "technical_context": {
            "current_price": 50000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BULLISH_EXPANDING"},
            "volatility_atr": {"value": 1000.0, "status": "EXTREME"},
        },
        "news_context": [{"headline": "Mercado estavel", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    res_extreme_status = rm.evaluate_order("BUY", 90, payload_extreme_status, current_exposure=10.0)
    assert res_extreme_status["action"] == "HOLD"
    assert "ATR EXTREME" in res_extreme_status["reason"]

    # Condition 2: ATR value / current_price > 0.05 (resulting in low Confianca Hibrida < 50%)
    payload_extreme_value = {
        "technical_context": {
            "current_price": 50000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BULLISH_EXPANDING"},
            "volatility_atr": {"value": 3000.0, "status": "NORMAL"},  # 3000 / 50000 = 0.06 (> 0.05)
        },
        "news_context": [{"headline": "Mercado estavel", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    # A dict ATR whose `status` says NORMAL while the ratio is 0.06 (> 0.05) is
    # exactly the shape that used to slip past the gate and rely on the hybrid
    # penalty instead. Since 2026-09-17 the gate blocks on the ratio too, so the
    # BUY is held earlier. Both blockers are listed as acceptable in the
    # docstring above; the security property under test is the HOLD.
    res_extreme_value = rm.evaluate_order("BUY", 90, payload_extreme_value, current_exposure=10.0)
    assert res_extreme_value["action"] == "HOLD"
    assert (
        "ATR EXTREME" in res_extreme_value["reason"]
        or "Confianca Hibrida" in res_extreme_value["reason"]
    ), f"flash-crash BUY blocked for an unrelated reason: {res_extreme_value['reason']}"


def test_red_team_false_positive_hack():
    """
    Scenario 2: test_red_team_false_positive_hack()
    - Mock a payload with bullish/neutral indicators, but the news context contains a news item
      containing the red flag word "hack".
    - Populate news_risk in the payload with has_negative_red_flag = True, risk_level = "ELEVATED",
      and matched terms containing "hack".
    - Verify that when LLM action is "BUY", the Risk Manager blocks it:
      rm.evaluate_order(...) returns "HOLD" due to "news red flag (hack)".
    """
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)

    payload = {
        "technical_context": {
            "current_price": 50000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BULLISH_EXPANDING"},
            "volatility_atr": {"value": 100.0, "status": "NORMAL"},
        },
        "news_context": [{"headline": "Exchange hack reports causing concern", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {
            "has_negative_red_flag": True,
            "has_untrusted_instruction": False,
            "risk_level": "ELEVATED",
            "matched_terms": ["hack"],
        },
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    res = rm.evaluate_order("BUY", 90, payload, current_exposure=10.0)
    assert res["action"] == "HOLD"
    assert "news red flag (hack)" in res["reason"]


def test_negative_news_can_confirm_bearish_sell_without_weakening_reliability():
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)
    payload = {
        "technical_context": {
            "current_price": 50_000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BEARISH_EXPANDING"},
            "volatility_atr": {"value": 100.0, "status": "NORMAL"},
        },
        "news_context": [{"headline": "Exchange hack confirmed", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {
            "has_negative_red_flag": True,
            "has_untrusted_instruction": False,
            "risk_level": "ELEVATED",
            "matched_terms": ["hack"],
        },
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    assert rm.calculate_system_reliability(payload) == 0.7
    assert rm.calculate_system_reliability(payload, action="BUY") == 0.7
    assert rm.calculate_system_reliability(payload, action="SELL") == 1.0

    result = rm.evaluate_order("SELL", 70, payload, current_exposure=20.0)
    assert result["action"] == "SELL"
    assert result["executed_size"] == 5.0
    assert "70.0%" in result["reason"]


def test_negative_news_does_not_confirm_sell_with_stale_news_or_oversold_rsi():
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)
    payload = {
        "technical_context": {
            "current_price": 50_000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BEARISH_EXPANDING"},
            "volatility_atr": {"value": 100.0, "status": "NORMAL"},
        },
        "news_context": [{"headline": "Exchange hack confirmed", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": True},
        "news_risk": {"has_negative_red_flag": True, "has_untrusted_instruction": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    assert rm.calculate_system_reliability(payload, action="SELL") == 0.42
    assert rm.evaluate_order("SELL", 70, payload, current_exposure=20.0)["action"] == "HOLD"

    payload["data_health"]["is_news_stale"] = False
    payload["technical_context"]["rsi"]["status"] = "OVERSOLD"
    assert rm.calculate_system_reliability(payload, action="SELL") == 0.7
    blocked = rm.evaluate_order("SELL", 90, payload, current_exposure=20.0)
    assert blocked["action"] == "HOLD"
    assert "RSI OVERSOLD" in blocked["reason"]


def test_red_team_all_in_suicidal():
    """
    Scenario 3: test_red_team_all_in_suicidal()
    - Mock a BUY recommendation from the LLM.
    - Verify two conditions under Risk Manager limits:
      a) If the current exposure is 200% (exceeding max exposure limit of 80% or 100%), the Risk Manager blocks it:
         rm.evaluate_order(...) returns "HOLD" due to max exposure limit.
      b) If the current exposure is normal (e.g., 10%), the Risk Manager allows the BUY, but the executed size
         is capped at max_allowed_risk_per_trade (5.0%).
    """
    # Test case A: Max exposure limit exceeded (e.g. max exposure is 100% and current is 200%)
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)

    payload = {
        "technical_context": {
            "current_price": 50000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BULLISH_EXPANDING"},
            "volatility_atr": {"value": 100.0, "status": "NORMAL"},
        },
        "news_context": [{"headline": "Mercado estavel", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    res_limit_exceeded = rm.evaluate_order("BUY", 90, payload, current_exposure=200.0)
    assert res_limit_exceeded["action"] == "HOLD"
    assert "Teto de alocacao" in res_limit_exceeded["reason"]

    # Test case B: Normal exposure (10%), allows buy but caps at max_allowed_risk_per_trade (5.0%)
    res_normal = rm.evaluate_order("BUY", 90, payload, current_exposure=10.0)
    assert res_normal["action"] == "BUY"
    assert res_normal["executed_size"] == 5.0


# ===========================================================================
# ACHADOS 2026-09-17 — fechados nesta sessao
#
# Os tres testes abaixo travam o comportamento corrigido. Se algum falhar, a
# mitigacao regrediu: nenhum deles deve ser afrouxado para "fazer passar".
# ===========================================================================


def test_hybrid_confidence_floor_is_exclusive_at_exactly_the_threshold():
    """Achado (a): exatamente 0.50 nao pode aprovar.

    A comparacao era `< 0.50`, entao conviction 100 com penalidade x0.5
    aprovava no piso exato, sem margem nenhuma. Agora o piso e exclusivo.
    """
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)
    payload = {
        "technical_context": {
            "current_price": 50000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "NEUTRAL"},
            # 10000 / 50000 = 0.20, muito acima do limiar de volatilidade
            "volatility_atr": {"value": 10000.0, "status": "EXTREME"},
        },
        "news_context": [{"headline": "Mercado estavel", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    # Confiabilidade 1.0 * 0.5 (ATR extremo) = 0.5. Conviction 100 -> hibrida 0.50 exata.
    assert rm.calculate_system_reliability(payload, action="SELL") == 0.5

    at_floor = rm.evaluate_order("SELL", 100, payload, current_exposure=10.0)
    assert at_floor["action"] == "HOLD"
    assert "Confianca Hibrida" in at_floor["reason"]
    assert at_floor["executed_size"] == 0.0

    # Consequencia, fixada aqui para nao virar efeito colateral invisivel: em ATR
    # extremo a confiabilidade e 0.5, entao nenhuma direcao alcanca o piso com o
    # teto de conviction do caminho vivo (80) -- o maximo seria 0.40. Isso ja era
    # verdade antes da correcao, porque 0.40 < 0.50. No caminho vivo o piso
    # exclusivo nao muda nada; ele so fecha o caso de conviction 100 vindo de
    # harness que nao aplica o teto (compare_llm_models.py).
    for conviction in (70, 80, 90):
        held = rm.evaluate_order("SELL", conviction, payload, current_exposure=10.0)
        assert held["action"] == "HOLD"
    # SELL nao tem bloqueio de ATR por desenho -- reduzir exposicao nao deve ser
    # proibido pela volatilidade que torna reduzir atraente. Mas isso nao torna
    # SELL executavel neste regime: ATR extremo limita sys_rel a 0.5, entao a
    # confianca nunca passa de 0.50 e o piso exclusivo segura o SELL tambem.
    # Este e o comportamento medido, e esta travado aqui de proposito para nao
    # ser lido como "da para reduzir posicao num crash".
    for conviction in (70, 80, 90, 100):
        held_sell = rm.evaluate_order("SELL", conviction, payload, current_exposure=10.0)
        assert held_sell["action"] == "HOLD"
        assert "Confianca Hibrida" in held_sell["reason"]

    # Controle positivo. Sem isto o teste passaria com um Risk Manager que
    # bloqueia tudo -- ele detecta a reversao do `<` para `<=`, mas nao um gate
    # quebrado. Um cenario limpo (ATR normal) tem que continuar aprovando.
    clean = {
        "technical_context": {
            "current_price": 50000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "NEUTRAL"},
            "volatility_atr": {"value": 100.0, "status": "NORMAL"},
        },
        "news_context": [{"headline": "Mercado estavel", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }
    assert rm.calculate_system_reliability(clean, action="SELL") == 1.0
    approved = rm.evaluate_order("SELL", 90, clean, current_exposure=10.0)
    assert approved["action"] == "SELL", "o gate esta bloqueando tudo"
    assert approved["executed_size"] > 0
    # E a margem ACIMA do piso tambem aprova (0.90 > 0.50), provando que o
    # criterio e "maior que 0.50" e nao "nunca aprovado".
    assert approved["reason"].startswith("Aprovado")


def test_directional_gate_blocks_when_rsi_or_macd_evidence_is_absent():
    """Achado (b): status ausente nao pode passar pelo gate direcional.

    Chave faltando produzia None, que nao casava com nenhuma blocklist, entao o
    gate aprovava sobre evidencia que nunca recebeu. Ausente, None, tipo errado e
    o literal UNKNOWN sao todos evidencia insuficiente.
    """
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)

    def _payload(technical_context: dict) -> dict:
        return {
            "technical_context": technical_context,
            "news_context": [{"headline": "Mercado estavel", "source": "pytest"}],
            "data_health": {"is_market_data_stale": False, "is_news_stale": False},
            "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
            "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
        }

    partial = _payload({"current_price": 50000.0, "volatility_atr": {"value": 100.0, "status": "NORMAL"}})
    for action in ("BUY", "SELL"):
        blocked = rm.evaluate_order(action, 90, partial, current_exposure=10.0)
        assert blocked["action"] == "HOLD", f"{action} aprovado sem rsi/macd"
        assert "ausente ou desconhecido" in blocked["reason"]
        assert blocked["executed_size"] == 0.0

    none_status = _payload({
        "current_price": 50000.0,
        "rsi": {"status": None},
        "macd": {"status": "NEUTRAL"},
        "volatility_atr": {"value": 100.0, "status": "NORMAL"},
    })
    assert rm.evaluate_order("BUY", 90, none_status, current_exposure=10.0)["action"] == "HOLD"

    unknown_status = _payload({
        "current_price": 50000.0,
        "rsi": {"status": "UNKNOWN"},
        "macd": {"status": "NEUTRAL"},
        "volatility_atr": {"value": 100.0, "status": "NORMAL"},
    })
    assert rm.evaluate_order("BUY", 90, unknown_status, current_exposure=10.0)["action"] == "HOLD"

    missing_macd = _payload({
        "current_price": 50000.0,
        "rsi": {"status": "NEUTRAL"},
        "volatility_atr": {"value": 100.0, "status": "NORMAL"},
    })
    blocked_macd = rm.evaluate_order("BUY", 90, missing_macd, current_exposure=10.0)
    assert blocked_macd["action"] == "HOLD"
    assert "MACD ausente ou desconhecido" in blocked_macd["reason"]

    # Controle: com AMBOS os status presentes e validos, o mesmo payload aprova.
    # Sem isto o teste passaria mesmo se o gate bloqueasse tudo.
    complete = _payload({
        "current_price": 50000.0,
        "rsi": {"status": "NEUTRAL"},
        "macd": {"status": "BULLISH_EXPANDING"},
        "volatility_atr": {"value": 100.0, "status": "NORMAL"},
    })
    approved = rm.evaluate_order("BUY", 90, complete, current_exposure=10.0)
    assert approved["action"] == "BUY"
    assert approved["executed_size"] == 5.0


def test_extreme_atr_ratio_blocks_buy_regardless_of_the_reported_status():
    """Achado (a), forma alternativa: status mentiroso nao escapa do gate.

    Um ATR escalar, ou um dict com status NORMAL, pulava o bloqueio de ATR
    EXTREME enquanto a penalidade de confiabilidade continuava aplicando x0.5.
    A divisao ratio/status era a origem do caminho de aprovacao de BUY no piso.
    """
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)

    def _payload(atr) -> dict:
        return {
            "technical_context": {
                "current_price": 50000.0,
                "rsi": {"status": "NEUTRAL"},
                "macd": {"status": "BULLISH_EXPANDING"},
                "volatility_atr": atr,
            },
            "news_context": [{"headline": "Mercado estavel", "source": "pytest"}],
            "data_health": {"is_market_data_stale": False, "is_news_stale": False},
            "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
            "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
        }

    for label, atr in (
        ("escalar 10000", 10000.0),
        ("dict status NORMAL", {"value": 10000.0, "status": "NORMAL"}),
    ):
        blocked = rm.evaluate_order("BUY", 90, _payload(atr), current_exposure=10.0)
        assert blocked["action"] == "HOLD", f"BUY aprovado com ATR extremo ({label})"
        assert "ATR EXTREME" in blocked["reason"]
        assert blocked["executed_size"] == 0.0

    # Controle: ATR pequeno com status NORMAL continua aprovando.
    normal = rm.evaluate_order("BUY", 90, _payload({"value": 100.0, "status": "NORMAL"}), current_exposure=10.0)
    assert normal["action"] == "BUY"

    # Tipos numericos reais sao aceitos, nao apenas int/float: um Decimal vindo
    # de outra camada nao pode ser lido como "ATR ausente".
    from decimal import Decimal

    accepted = rm.evaluate_order(
        "BUY", 90, _payload({"value": Decimal("100.0"), "status": "NORMAL"}), current_exposure=10.0
    )
    assert accepted["action"] == "BUY", "Decimal no ATR foi tratado como ausente"
    assert accepted["executed_size"] == 5.0
    assert rm.evaluate_order(
        "BUY", 90, _payload(Decimal("100.0")), current_exposure=10.0
    )["action"] == "BUY"
    # Decimal nao-finito continua sendo evidencia invalida.
    assert rm.evaluate_order(
        "BUY", 90, _payload({"value": Decimal("Infinity"), "status": "NORMAL"}), current_exposure=10.0
    )["action"] == "HOLD"

    # Um objeto com `__float__` arbitrario NAO e evidencia de ATR. Nao basta
    # aceitar "o que `float()` aceita": `float(BoolLike())` e 1.0 e aprovava BUY
    # sobre volatilidade booleana.
    class DuckFloat:
        def __float__(self):
            return 1.0

    assert rm._atr_value({"volatility_atr": DuckFloat()}) != rm._atr_value({"volatility_atr": DuckFloat()})  # NaN
    assert rm.evaluate_order("BUY", 90, _payload(DuckFloat()), current_exposure=10.0)["action"] == "HOLD"

    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        # `numpy.bool_` nao e instancia de `bool`, entao escapava da checagem.
        assert rm.evaluate_order(
            "BUY", 90, _payload(np.bool_(True)), current_exposure=10.0
        )["action"] == "HOLD"
        # Escalares numericos do numpy continuam aceitos.
        assert rm.evaluate_order(
            "BUY", 90, _payload(np.float64(100.0)), current_exposure=10.0
        )["action"] == "BUY"

    # Volatilidade ausente nao pode parecer mercado calmo (mesma classe de defeito).
    # A chave AUSENTE e testada removendo-a de fato: passar um dict para o helper
    # `_payload(atr)` deixaria `volatility_atr` presente, exercitando outro ramo.
    missing_key = _payload({"value": 100.0, "status": "NORMAL"})
    del missing_key["technical_context"]["volatility_atr"]
    assert "volatility_atr" not in missing_key["technical_context"]
    missing_blocked = rm.evaluate_order("BUY", 90, missing_key, current_exposure=10.0)
    assert missing_blocked["action"] == "HOLD"
    assert "ATR ausente" in missing_blocked["reason"]

    for label, atr in (
        ("dict sem value", {"status": "NORMAL"}),
        ("value None", {"value": None, "status": "NORMAL"}),
        ("ATR nulo", None),
        ("ATR NaN", float("nan")),
        ("ATR negativo", -1.0),
        ("ATR booleano", True),
        ("ATR string vazia", ""),
        ("ATR lista", []),
    ):
        blocked = rm.evaluate_order("BUY", 90, _payload(atr), current_exposure=10.0)
        assert blocked["action"] == "HOLD", f"BUY aprovado com volatilidade ausente ({label})"
        assert "ATR" in blocked["reason"], f"motivo nao cita ATR ({label}): {blocked['reason']}"


def test_container_subclasses_do_not_raise_through_the_gate():
    """Subclasses de dict/list nao podem abortar o ciclo.

    `isinstance(value, dict)` aceita uma subclasse cujo `get` levanta, e
    `usable_news_count` iterava uma subclasse de list. Cada uma propagava a
    excecao em vez de devolver o HOLD auditado. O gate usa tipo builtin exato,
    igual ao leitor de auditoria.
    """
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)

    class HostileDict(dict):
        def get(self, *args, **kwargs):
            raise ZeroDivisionError("boom")

    class HostileList(list):
        def __iter__(self):
            raise ZeroDivisionError("boom")

    class HostileStr(str):
        def __str__(self):
            raise ZeroDivisionError("boom")

    def _base(**overrides) -> dict:
        payload = {
            "technical_context": {
                "current_price": 50000.0,
                "rsi": {"status": "NEUTRAL"},
                "macd": {"status": "BULLISH_EXPANDING"},
                "volatility_atr": {"value": 100.0, "status": "NORMAL"},
            },
            "news_context": [{"headline": "Mercado estavel"}],
            "data_health": {"is_market_data_stale": False, "is_news_stale": False},
            "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
            "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
        }
        payload.update(overrides)
        return payload

    # `portfolio_context` is not a reliability input (by design); it is covered
    # by the evaluate_order assertion only.
    reliability_inputs = (
        ("payload subclass", HostileDict(_base())),
        ("data_health subclass", _base(data_health=HostileDict({"is_market_data_stale": False, "is_news_stale": False}))),
        ("news_risk subclass", _base(news_risk=HostileDict({"has_negative_red_flag": False, "has_untrusted_instruction": False}))),
        ("technical_context subclass", _base(technical_context=HostileDict({"current_price": 50000.0}))),
        ("news_context subclass", _base(news_context=HostileList([{"headline": "x"}]))),
    )
    for label, payload in reliability_inputs + (
        ("portfolio_context subclass", _base(portfolio_context=HostileDict({"max_allowed_risk_per_trade": 5.0}))),
    ):
        result = rm.evaluate_order("BUY", 90, payload, current_exposure=10.0)
        assert result["action"] == "HOLD", f"{label} nao fechou"
        assert result["executed_size"] == 0.0, label
    for label, payload in reliability_inputs:
        assert rm.calculate_system_reliability(payload, action="BUY") == 0.0, label

    # A acao tambem nao pode explodir por causa de uma subclasse de str.
    assert rm.evaluate_order(HostileStr("BUY"), 90, _base(), current_exposure=10.0)["action"] == "HOLD"

    # Um item de noticia que nao e dict builtin nao conta como noticia presente,
    # mas tambem nao derruba a avaliacao.
    mixed_news = _base(news_context=[HostileDict({"headline": "x"})])
    assert rm.evaluate_order("BUY", 90, mixed_news, current_exposure=10.0)["action"] == "BUY"


def test_hostile_scalar_subclasses_do_not_raise_through_the_gate():
    """`float()`/`str()` rodam codigo arbitrario: uma subclasse hostil nao pode
    abortar o ciclo.

    `except (TypeError, ValueError, OverflowError)` nao captura um `RuntimeError`
    vindo de `__float__`, e o mesmo vale para `strip()`/`__str__`. Cada leitura
    numerica passa por `safe_float` e cada rotulo de motivo e construido em
    try/except.
    """
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)

    class FloatBoom(float):
        def __float__(self):
            raise RuntimeError("boom")

    class StrBoom(str):
        def strip(self, *args, **kwargs):
            raise RuntimeError("boom")

    class TermBoom:
        def __str__(self):
            raise RuntimeError("boom")

    def _base(**overrides) -> dict:
        payload = {
            "technical_context": {
                "current_price": 50000.0,
                "rsi": {"status": "NEUTRAL"},
                "macd": {"status": "BULLISH_EXPANDING"},
                "volatility_atr": {"value": 100.0, "status": "NORMAL"},
            },
            "news_context": [{"headline": "Mercado estavel"}],
            "data_health": {"is_market_data_stale": False, "is_news_stale": False},
            "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
            "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
        }
        payload.update(overrides)
        return payload

    hostile_calls = (
        ("conviction", lambda: rm.evaluate_order("BUY", FloatBoom(90.0), _base(), 10.0)),
        ("exposure", lambda: rm.evaluate_order("BUY", 90, _base(), FloatBoom(10.0))),
        (
            "max_allowed",
            lambda: rm.evaluate_order(
                "BUY", 90, _base(portfolio_context={"max_allowed_risk_per_trade": FloatBoom(5.0)}), 10.0
            ),
        ),
        (
            "current_price",
            lambda: rm.evaluate_order(
                "BUY",
                90,
                _base(technical_context={"current_price": FloatBoom(50000.0), "rsi": {"status": "NEUTRAL"}, "macd": {"status": "BULLISH_EXPANDING"}, "volatility_atr": {"value": 100.0, "status": "NORMAL"}}),
                10.0,
            ),
        ),
        (
            "volatility_atr str",
            lambda: rm.evaluate_order(
                "BUY",
                90,
                _base(technical_context={"current_price": 50000.0, "rsi": {"status": "NEUTRAL"}, "macd": {"status": "BULLISH_EXPANDING"}, "volatility_atr": StrBoom("100.0")}),
                10.0,
            ),
        ),
        (
            "matched_terms item",
            lambda: rm.evaluate_order(
                "BUY",
                90,
                _base(news_risk={"has_negative_red_flag": True, "has_untrusted_instruction": False, "matched_terms": [TermBoom()]}),
                10.0,
            ),
        ),
        (
            "daily_drawdown",
            lambda: rm.evaluate_order(
                "BUY",
                90,
                _base(portfolio_context={"max_allowed_risk_per_trade": 5.0, "daily_drawdown_percentage": FloatBoom(1.0)}),
                10.0,
            ),
        ),
    )
    for label, call in hostile_calls:
        result = call()
        assert result["action"] == "HOLD", f"{label} nao fechou"
        assert result["executed_size"] == 0.0
        assert result["reason"], label

    # A confiabilidade tambem nao pode levantar com preco hostil.
    hostile_tech = {"current_price": FloatBoom(50000.0), "volatility_atr": 100.0}
    assert rm.calculate_system_reliability(_base(technical_context=hostile_tech), action="BUY") == 0.0
    # E o motivo do red flag nao pode explodir ao imprimir o termo.
    reason = rm.evaluate_order(
        "BUY",
        90,
        _base(news_risk={"has_negative_red_flag": True, "has_untrusted_instruction": False, "matched_terms": [TermBoom()]}),
        10.0,
    )["reason"]
    assert "news red flag" in reason

    # Controle positivo: os mesmos payloads com valores reais continuam aprovando.
    assert rm.evaluate_order("BUY", 90, _base(), 10.0)["action"] == "BUY"


def test_unhashable_indicator_status_fails_closed():
    """Achado 2026-09-17: status nao-hashable derrubava o gate.

    `rsi_status not in KNOWN_RSI_STATUS` faz membership em frozenset. Um dict ou
    lista como status e unhashable, entao `in` levanta `TypeError` em vez de
    fechar. `_status_of` agora devolve apenas `str` simples ou None.
    """
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)

    class HashBoom(str):
        def __hash__(self):
            raise RuntimeError("hash boom")

    def _payload(rsi_status, macd_status, *, red_flag: bool = False) -> dict:
        return {
            "technical_context": {
                "current_price": 50000.0,
                "rsi": {"status": rsi_status},
                "macd": {"status": macd_status},
                "volatility_atr": {"value": 100.0, "status": "NORMAL"},
            },
            "news_context": [{"headline": "Mercado estavel"}],
            "data_health": {"is_market_data_stale": False, "is_news_stale": False},
            "news_risk": {
                "has_negative_red_flag": red_flag,
                "has_untrusted_instruction": False,
                "matched_terms": ["hack"] if red_flag else [],
            },
            "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
        }

    for label, rsi_status, macd_status in (
        ("rsi dict", {"a": 1}, "NEUTRAL"),
        ("rsi list", [1], "NEUTRAL"),
        ("macd dict", "NEUTRAL", {"a": 1}),
        ("macd list", "NEUTRAL", [1]),
        ("rsi hash boom", HashBoom("NEUTRAL"), "NEUTRAL"),
    ):
        # Sem red flag, o bloqueio vem do status; com red flag, o do red flag
        # vence (hazard tem prioridade), mas nos dois casos nao pode levantar.
        for action in ("BUY", "SELL"):
            blocked = rm.evaluate_order(action, 90, _payload(rsi_status, macd_status), current_exposure=10.0)
            assert blocked["action"] == "HOLD", f"{label} nao fechou com {action}"
            assert blocked["executed_size"] == 0.0
            assert "ausente ou desconhecido" in blocked["reason"], blocked["reason"]
        with_flag = rm.evaluate_order(
            "BUY", 90, _payload(rsi_status, macd_status, red_flag=True), current_exposure=10.0
        )
        assert with_flag["action"] == "HOLD"
        assert "news red flag" in with_flag["reason"]
        # `calculate_system_reliability` chega ao mesmo status via
        # `_negative_news_confirms_sell`; tambem nao pode levantar.
        assert rm.calculate_system_reliability(
            _payload(rsi_status, macd_status, red_flag=True), action="SELL"
        ) in (0.0, 0.7, 1.0)

    # O status do ATR tambem precisa ser `str` exato: uma subclasse com `__eq__`
    # hostil explode na comparacao com "EXTREME". O status hostil e descartado
    # (vira None), entao quem decide passa a ser o ratio -- e com ratio extremo
    # o BUY tem de fechar mesmo assim.
    class EqBoom(str):
        def __eq__(self, other):
            raise RuntimeError("eq boom")

    hostile_atr_status = _payload("NEUTRAL", "BULLISH_EXPANDING")
    hostile_atr_status["technical_context"]["volatility_atr"] = {
        "value": 10000.0,  # 10000 / 50000 = 0.20, extremo
        "status": EqBoom("NORMAL"),
    }
    held = rm.evaluate_order("BUY", 90, hostile_atr_status, current_exposure=10.0)
    assert held["action"] == "HOLD"
    assert "ATR EXTREME" in held["reason"]
    # Com status hostil e ATR pequeno, o status e ignorado e o BUY aprova.
    benign_atr = _payload("NEUTRAL", "BULLISH_EXPANDING")
    benign_atr["technical_context"]["volatility_atr"] = {"value": 100.0, "status": EqBoom("NORMAL")}
    assert rm.evaluate_order("BUY", 90, benign_atr, current_exposure=10.0)["action"] == "BUY"

    # Controle positivo: status validos continuam decidindo normalmente.
    assert rm.evaluate_order("BUY", 90, _payload("NEUTRAL", "BULLISH_EXPANDING"), current_exposure=10.0)["action"] == "BUY"
    blocked_sell = rm.evaluate_order("SELL", 90, _payload("NEUTRAL", "BULLISH_EXPANDING"), current_exposure=10.0)
    assert blocked_sell["action"] == "HOLD"
    assert "MACD BULLISH_EXPANDING" in blocked_sell["reason"]


def test_risk_inputs_reject_strings_instead_of_coercing_them():
    """Achados 2026-09-17: os inputs de risco nao aceitam string numerica.

    `safe_float` aceita string por padrao, e a primeira versao do fix deixou
    `current_exposure`/`conviction` passarem como string convertida. Isso
    transformava um `TypeError` do `HEAD` em aprovacao -- uma relaxacao. Os
    inputs de risco (conviccao, exposicao, limite por trade, drawdown) usam
    `allow_numeric_string=False` porque o pipeline sempre passa numeros.
    """
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    def _payload() -> dict:
        return {
            "technical_context": {
                "current_price": 50000.0,
                "rsi": {"status": "NEUTRAL"},
                "macd": {"status": "NEUTRAL"},
                "volatility_atr": {"value": 100.0, "status": "NORMAL"},
            },
            "news_context": [{"headline": "Mercado estavel"}],
            "data_health": {"is_market_data_stale": False, "is_news_stale": False},
            "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
            "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
        }

    for label, result in (
        ("exposure '30'", rm.evaluate_order("BUY", 90, _payload(), "30")),
        ("exposure '85'", rm.evaluate_order("BUY", 90, _payload(), "85")),
        ("exposure '30' SELL", rm.evaluate_order("SELL", 90, _payload(), "30")),
        ("conviction '90'", rm.evaluate_order("BUY", "90", _payload(), 10.0)),
    ):
        assert result["action"] == "HOLD", f"{label} nao fechou"
        assert result["executed_size"] == 0.0
        assert "Risk input" in result["reason"], result["reason"]

    # Limite por trade ausente ou nao numerico fecha, sem default.
    for label, portfolio in (
        ("portfolio vazio", {}),
        ("sem a chave", {"other": 1}),
        ("string", {"max_allowed_risk_per_trade": "5"}),
        ("None", {"max_allowed_risk_per_trade": None}),
    ):
        payload = _payload()
        payload["portfolio_context"] = portfolio
        blocked = rm.evaluate_order("BUY", 90, payload, 10.0)
        assert blocked["action"] == "HOLD", f"{label} nao fechou"
        assert "Limite por trade" in blocked["reason"], blocked["reason"]

    # Controle positivo: numeros reais continuam funcionando nos dois limites.
    above = rm.evaluate_order("BUY", 90, _payload(), 85.0)
    assert above["action"] == "HOLD"
    assert "Teto de alocacao" in above["reason"]
    below = rm.evaluate_order("BUY", 90, _payload(), 30.0)
    assert below["action"] == "BUY"
    assert below["executed_size"] == 5.0
    sell = rm.evaluate_order("SELL", 90, _payload(), 2.5)
    assert sell["action"] == "SELL"
    assert sell["executed_size"] == 2.5


def test_malformed_payload_fails_closed_instead_of_raising():
    """Achado (c): payload malformado retorna HOLD, nunca levanta excecao.

    Antes, `payload.get(key, {})` so protegia chave AUSENTE. Chave presente com
    None (ou tipo errado) estourava AttributeError/TypeError, e um crash nao e
    uma decisao de seguranca auditada.
    """
    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)
    good_tech = {
        "current_price": 50000.0,
        "rsi": {"status": "NEUTRAL"},
        "macd": {"status": "BULLISH_EXPANDING"},
        "volatility_atr": {"value": 100.0, "status": "NORMAL"},
    }
    good_news = [{"headline": "Mercado estavel", "source": "pytest"}]
    good_health = {"is_market_data_stale": False, "is_news_stale": False}
    good_risk = {"has_negative_red_flag": False, "has_untrusted_instruction": False}
    good_portfolio = {"max_allowed_risk_per_trade": 5.0}

    def _base() -> dict:
        return {
            "technical_context": dict(good_tech),
            "news_context": list(good_news),
            "data_health": dict(good_health),
            "news_risk": dict(good_risk),
            "portfolio_context": dict(good_portfolio),
        }

    malformed = {
        "news_context": None,
        "news_risk": None,
        "data_health": None,
        "technical_context": None,
        "portfolio_context": None,
    }
    for field, value in malformed.items():
        for action in ("BUY", "SELL", "HOLD"):
            payload = _base()
            payload[field] = value
            result = rm.evaluate_order(action, 90, payload, current_exposure=10.0)
            assert result["action"] == "HOLD", f"{field}=None com {action} nao fechou"
            assert result["executed_size"] == 0.0
            assert result["reason"], f"{field}=None nao registrou motivo"

    # Tipo errado no lugar de um objeto. `news_context` entra na lista: uma
    # string/dict e "verdadeira" e tem len > 0, entao passava como "ha noticia".
    for field in ("technical_context", "data_health", "news_risk", "portfolio_context", "news_context"):
        for wrong in ("texto", ["lista"] if field != "news_context" else {"a": 1}, 42):
            payload = _base()
            payload[field] = wrong
            result = rm.evaluate_order("BUY", 90, payload, current_exposure=10.0)
            assert result["action"] == "HOLD", f"{field}={wrong!r} nao fechou"

    # `matched_terms` invalido dentro de um red flag nao pode derrubar o motivo.
    for wrong in (None, "hack", 42):
        payload = _base()
        payload["news_risk"] = {
            "has_negative_red_flag": True,
            "has_untrusted_instruction": False,
            "matched_terms": wrong,
        }
        result = rm.evaluate_order("BUY", 90, payload, current_exposure=10.0)
        assert result["action"] == "HOLD"
        assert "news red flag" in result["reason"]
        assert "--" not in result["reason"]

    # Chave estrutural AUSENTE e ausencia de evidencia, nao "tudo fresco": sem
    # data_health o flag de stale leria como False e sem news_risk o de injecao
    # tambem. Mesma classe do defeito (b).
    for missing in ("data_health", "news_risk", "technical_context"):
        payload = _base()
        del payload[missing]
        for action in ("BUY", "SELL"):
            result = rm.evaluate_order(action, 90, payload, current_exposure=10.0)
            assert result["action"] == "HOLD", f"{missing} ausente aprovou {action}"
            assert "ausente ou malformado" in result["reason"]
        assert rm.calculate_system_reliability(payload, action="BUY") == 0.0

    # Um mapa VAZIO e a mesma classe: `{}` e um dict, entao `_as_mapping` o
    # aceitava e todo `.get(flag)` ficava falsy -- "fresco" e "sem injecao" sem
    # que o payload tivesse afirmado isso. O produtor real sempre emite os flags.
    for field, value, expected in (
        ("data_health", {}, "data_health"),
        ("news_risk", {}, "news_risk"),
        ("data_health", {"is_market_data_stale": False}, "is_news_stale"),
        ("news_risk", {"has_negative_red_flag": False}, "has_untrusted_instruction"),
        # Presenca nao basta: o flag precisa SER booleano. Um valor falsy nao-bool
        # (None, 0, "") tambem lia como "fresco"/"sem injecao".
        ("data_health", {"is_market_data_stale": None, "is_news_stale": False}, "is_market_data_stale"),
        ("data_health", {"is_market_data_stale": 0, "is_news_stale": False}, "is_market_data_stale"),
        ("data_health", {"is_market_data_stale": "", "is_news_stale": False}, "is_market_data_stale"),
        ("news_risk", {"has_negative_red_flag": None, "has_untrusted_instruction": False}, "has_negative_red_flag"),
        ("news_risk", {"has_negative_red_flag": 0, "has_untrusted_instruction": False}, "has_negative_red_flag"),
        ("news_risk", {"has_negative_red_flag": False, "has_untrusted_instruction": ""}, "has_untrusted_instruction"),
    ):
        payload = _base()
        payload[field] = value
        for action in ("BUY", "SELL"):
            blocked = rm.evaluate_order(action, 90, payload, current_exposure=10.0)
            assert blocked["action"] == "HOLD", f"{field}={value} aprovou {action}"
            assert expected in blocked["reason"], f"motivo nao cita {expected}: {blocked['reason']}"
        assert rm.calculate_system_reliability(payload, action="BUY") == 0.0

    # Um booleano verdadeiro continua sendo um hazard valido, decidido pelo flag e
    # nao pela presenca.
    stale_true = _base()
    stale_true["data_health"] = {"is_market_data_stale": True, "is_news_stale": False}
    stale_blocked = rm.evaluate_order("BUY", 90, stale_true, current_exposure=10.0)
    assert stale_blocked["action"] == "HOLD"
    assert "market data stale" in stale_blocked["reason"]

    # Entradas de news_context que nao sao registros de noticia nao contam como
    # noticia presente: `len()` as lia como contexto valido.
    for label, bogus_news in (
        ("[None]", [None]),
        ("[42]", [42]),
        ("['texto']", ["texto"]),
        ("[{}]", [{}]),
        ("[{'headline': ''}]", [{"headline": ""}]),
        ("[{'headline': None}]", [{"headline": None}]),
    ):
        payload = _base()
        payload["news_context"] = bogus_news
        # Sem noticia utilizavel o piso sobe para 80, entao 90 ainda passa dele.
        # O que se testa e a confiabilidade: lista de lixo nao pode ler como 1.0.
        assert rm.calculate_system_reliability(payload, action="BUY") == 0.7, label

    # Uma lista com pelo menos um registro real conta como noticia presente.
    payload = _base()
    payload["news_context"] = [None, {"headline": "real"}, 42]
    assert rm.calculate_system_reliability(payload, action="BUY") == 1.0

    # Sub-objeto malformado: rsi/macd como None.
    for indicator in ("rsi", "macd"):
        payload = _base()
        payload["technical_context"][indicator] = None
        assert rm.evaluate_order("BUY", 90, payload, current_exposure=10.0)["action"] == "HOLD"

    # `calculate_system_reliability` e chamado fora do evaluate_order em main.py;
    # tambem nao pode levantar. Ele le apenas news_context, data_health,
    # news_risk e technical_context -- portfolio_context nao entra na conta.
    for field in ("news_context", "news_risk", "data_health", "technical_context"):
        for wrong in (None, "texto", 42):
            payload = _base()
            payload[field] = wrong
            reliability = rm.calculate_system_reliability(payload, action="BUY")
            assert reliability == 0.0, f"{field}={wrong!r} deveria zerar a confiabilidade"

    # Controle: o payload intacto continua aprovando.
    assert rm.evaluate_order("BUY", 90, _base(), current_exposure=10.0)["action"] == "BUY"
