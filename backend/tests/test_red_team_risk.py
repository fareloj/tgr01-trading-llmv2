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
        "news_risk": {"has_negative_red_flag": False},
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
        "news_risk": {"has_negative_red_flag": False},
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
        "news_risk": {"has_negative_red_flag": False},
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
        "news_risk": {"has_negative_red_flag": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    # conviction of 90% with system reliability penalized by 0.5 (since ATR / price > 0.05)
    # results in hybrid confidence of 90% * 0.5 = 45% (< 50% threshold)
    res_extreme_value = rm.evaluate_order("BUY", 90, payload_extreme_value, current_exposure=10.0)
    assert res_extreme_value["action"] == "HOLD"
    assert "Confianca Hibrida" in res_extreme_value["reason"]


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
            "risk_level": "ELEVATED",
            "matched_terms": ["hack"],
        },
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    res = rm.evaluate_order("BUY", 90, payload, current_exposure=10.0)
    assert res["action"] == "HOLD"
    assert "news red flag (hack)" in res["reason"]


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
        "news_risk": {"has_negative_red_flag": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    res_limit_exceeded = rm.evaluate_order("BUY", 90, payload, current_exposure=200.0)
    assert res_limit_exceeded["action"] == "HOLD"
    assert "Teto de alocacao" in res_limit_exceeded["reason"]

    # Test case B: Normal exposure (10%), allows buy but caps at max_allowed_risk_per_trade (5.0%)
    res_normal = rm.evaluate_order("BUY", 90, payload, current_exposure=10.0)
    assert res_normal["action"] == "BUY"
    assert res_normal["executed_size"] == 5.0
