from copy import deepcopy

import pytest

from backend.tests.compare_historical_campaigns import compare_campaign_reports, sampling_signature


def _report(mode: str, action: str = "HOLD", price: float = 100.0) -> dict:
    result = {
        "variant": "balanced",
        "window_id": "uptrend-01",
        "regime": "UPTREND",
        "timestamp": 100,
        "status": "OK",
        "price": price,
        "rsi": {"value": 40, "status": "NEUTRAL"},
        "macd": {"histogram": 1, "status": "BULLISH_EXPANDING"},
        "atr": 1.0,
        "llm_action": action,
        "risk_action": action,
        "risk_reason": "test",
        "llm_technical_failure": False,
        "horizons": {
            "5": {
                "maturity": "matured",
                "risk": {"status": "good", "directional_edge_after_cost_pct": 0.4},
            }
        },
    }
    return {
        "campaign_id": mode,
        "status": "COMPLETED",
        "config": {
            "asset": "BTC/BRL",
            "timeframe": "1m",
            "from_ts": 0,
            "to_ts": 1000,
            "horizons_minutes": [5],
            "threshold_pct": 0.2,
            "exposure_pct": 40,
            "news_mode": mode,
        },
        "windows": [
            {
                "id": "uptrend-01",
                "regime": "UPTREND",
                "start_ts": 0,
                "end_ts": 600,
                "cycle_timestamps": [100],
            }
        ],
        "results": [result],
    }


def test_comparison_pairs_identical_samples_and_reports_action_changes():
    historical = _report("historical")
    neutral = _report("neutral-fresh", action="BUY")

    report = compare_campaign_reports([historical, neutral])

    assert sampling_signature(historical) == sampling_signature(neutral)
    assert report["paired_points"] == 1
    assert report["mode_summary"]["neutral-fresh"]["risk_actions"] == {"BUY": 1}
    assert report["mode_summary"]["neutral-fresh"]["horizons"]["5"]["directional_good"] == 1
    assert report["mode_summary"]["historical"]["horizons"]["5"]["directional_samples"] == 0
    assert sum(report["paired_changes"].values()) == 1


def test_comparison_rejects_changed_technical_inputs():
    historical = _report("historical")
    neutral = _report("neutral-fresh", price=101.0)

    with pytest.raises(ValueError, match="technical payload changed"):
        compare_campaign_reports([historical, neutral])


def test_comparison_rejects_incomplete_mode_matrix():
    historical_a = _report("historical")
    neutral_a = _report("neutral-fresh")
    historical_b = deepcopy(historical_a)
    historical_b["campaign_id"] = "second-period"
    historical_b["config"]["from_ts"] = 2000
    historical_b["config"]["to_ts"] = 3000
    historical_b["windows"][0]["start_ts"] = 2000
    historical_b["windows"][0]["end_ts"] = 2600
    historical_b["windows"][0]["cycle_timestamps"] = [2100]
    historical_b["results"][0]["timestamp"] = 2100

    with pytest.raises(ValueError, match="matrix is incomplete"):
        compare_campaign_reports([historical_a, neutral_a, historical_b])


def test_comparison_rejects_duplicate_mode_for_sampling_group():
    historical = _report("historical")

    with pytest.raises(ValueError, match="duplicate news mode"):
        compare_campaign_reports([historical, deepcopy(historical)])
