from types import SimpleNamespace

import pytest

from backend.evaluation.historical_campaign import (
    classify_action_after_costs,
    evaluate_result_horizons,
    find_future_candle,
    freeze_windows,
    round_trip_cost_pct,
    select_non_overlapping_windows,
    summarize_results,
)
from backend.tests.compare_prompt_profiles import PromptProfileRunner


def _window(label, start, end, move, volatility=0.1):
    return SimpleNamespace(
        label=label,
        start_ts=start,
        end_ts=end,
        start_price=100.0,
        end_price=100.0 * (1 + move / 100),
        move_pct=move,
        volatility_pct=volatility,
        candles=61,
    )


def test_regime_selection_is_ranked_and_non_overlapping():
    windows = [
        _window("UPTREND", 0, 3600, 2.0),
        _window("UPTREND", 7200, 10800, 1.5),
        _window("DOWNTREND", 1000, 4600, -3.0),  # overlaps the strongest up window
        _window("DOWNTREND", 14400, 18000, -1.2),
        _window("SIDEWAYS", 21600, 25200, 0.01, 0.03),
    ]

    selected = select_non_overlapping_windows(windows, per_regime=1)

    assert [(regime, item.start_ts) for regime, item in selected] == [
        ("UPTREND", 0),
        ("DOWNTREND", 14400),
        ("SIDEWAYS", 21600),
    ]


def test_freeze_windows_uses_the_same_spaced_timestamps_for_every_variant():
    selected = [("UPTREND", _window("UPTREND", 0, 600, 1.0))]

    frozen = freeze_windows(selected, range(0, 601, 60), cycles=3, step_seconds=180)

    assert frozen[0].cycle_timestamps == (0, 180, 360)
    assert frozen[0].expected_action == "BUY"


def test_future_candle_distinguishes_gap_from_immature_horizon():
    candles = [{"timestamp": 100, "close": 100.0}, {"timestamp": 1000, "close": 101.0}]

    assert find_future_candle(
        candles, decision_timestamp=100, horizon_minutes=5, max_delay_seconds=90
    ) == ("data_gap", None)
    assert find_future_candle(
        candles, decision_timestamp=1000, horizon_minutes=5, max_delay_seconds=90
    ) == ("not_matured", None)


def test_cost_adjustment_scores_long_buy_and_long_reduction_sell():
    cost = round_trip_cost_pct(fee_rate=0.003, slippage_rate=0.0005)

    one_way = cost / 2
    buy = classify_action_after_costs(
        "BUY", raw_move_pct=1.2, buy_cost_pct=cost, sell_cost_pct=one_way, threshold_pct=0.2
    )
    sell = classify_action_after_costs(
        "SELL", raw_move_pct=-1.2, buy_cost_pct=cost, sell_cost_pct=one_way, threshold_pct=0.2
    )
    hold = classify_action_after_costs(
        "HOLD", raw_move_pct=1.2, buy_cost_pct=cost, sell_cost_pct=one_way, threshold_pct=0.2
    )

    assert cost == pytest.approx(0.7)
    assert buy["status"] == "good"
    assert sell["status"] == "good"
    assert buy["directional_edge_after_cost_pct"] == pytest.approx(0.5)
    assert sell["directional_edge_after_cost_pct"] == pytest.approx(0.85)
    assert hold["status"] == "missed_upside"


def test_horizon_evaluation_and_summary_keep_risk_and_llm_separate():
    result = {
        "variant": "balanced",
        "window_id": "uptrend-01",
        "regime": "UPTREND",
        "expected_action": "BUY",
        "timestamp": 100,
        "status": "OK",
        "price": 100.0,
        "llm_action": "BUY",
        "risk_action": "HOLD",
        "executed_size": 0.0,
    }
    result["horizons"] = evaluate_result_horizons(
        result,
        [{"timestamp": 100, "close": 100.0}, {"timestamp": 400, "close": 102.0}],
        horizons=[5],
        threshold_pct=0.2,
        fee_rate=0.0,
        slippage_rate=0.0,
        max_delay_seconds=90,
    )

    summary = summarize_results([result], [5])["balanced"]

    assert result["horizons"]["5"]["llm"]["status"] == "good"
    assert result["horizons"]["5"]["risk"]["status"] == "missed_upside"
    assert summary["llm_to_risk"] == {"BUY->HOLD": 1}
    assert summary["horizons"]["5"]["directional_samples"] == 0
    assert summary["horizons"]["5"]["missed_upside"] == 1
    assert summary["horizons"]["5"]["llm_conviction_calibration"]["0-49"]["samples"] == 1


def test_prompt_profile_runner_uses_bounded_gpt_oss_output_and_rotates_keys():
    runner = object.__new__(PromptProfileRunner)
    runner.model = "openai/gpt-oss-120b"
    runner.api_keys = ["first", "second"]
    runner.key_index = 0
    runner._build_client = lambda: f"client-{runner.key_index}"

    assert runner._request_limits() == {"max_completion_tokens": 600, "reasoning_effort": "low"}
    assert runner._rotate_key() is True
    assert runner.key_index == 1
    assert runner.client == "client-1"
    assert runner._rotate_key() is False
