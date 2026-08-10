from __future__ import annotations

import numpy as np

from backend.ml.policy import (
    ProbabilityPolicyConfig,
    evaluate_probability_policy,
    fit_probability_policy,
    probability_signals,
)


def _logits(rows: list[list[float]]) -> np.ndarray:
    return np.asarray(rows, dtype=np.float64)[:, None, :]


def test_probability_policy_abstains_without_a_clear_directional_edge():
    logits = _logits(
        [
            [0.0, 0.0, 3.0],
            [3.0, 0.0, 0.0],
            [0.2, 0.3, 0.1],
        ]
    )
    config = ProbabilityPolicyConfig(
        horizon_index=0,
        horizon_minutes=15,
        confidence_threshold=0.60,
        directional_margin=0.20,
        round_trip_cost_pct=0.15,
    )

    assert probability_signals(logits, config).tolist() == [1, -1, 0]


def test_probability_policy_skips_overlapping_trades_and_applies_costs():
    logits = _logits([[0.0, 0.0, 4.0]] * 4)
    returns = np.asarray([[0.5], [0.5], [0.5], [0.5]], dtype=np.float32)
    timestamps = np.asarray([0, 5 * 60, 15 * 60, 30 * 60], dtype=np.int64)
    config = ProbabilityPolicyConfig(
        horizon_index=0,
        horizon_minutes=15,
        confidence_threshold=0.60,
        directional_margin=0.20,
        round_trip_cost_pct=0.15,
    )

    metrics = evaluate_probability_policy(logits, returns, timestamps, config)

    assert metrics["signals"] == 4
    assert metrics["trades"] == 3
    assert metrics["overlap_skipped"] == 1
    assert np.isclose(metrics["average_trade_return_pct"], 0.35)


def test_policy_search_disables_unprofitable_candidates():
    logits = _logits([[0.0, 0.0, 4.0]] * 20)
    returns = np.full((20, 1), -0.4, dtype=np.float32)
    timestamps = np.arange(20, dtype=np.int64) * 15 * 60

    selected, diagnostics = fit_probability_policy(
        logits,
        returns,
        timestamps,
        horizon_index=0,
        horizon_minutes=15,
        round_trip_cost_pct=0.15,
        confidence_thresholds=(0.50,),
        directional_margins=(0.10,),
        minimum_trades=5,
    )

    assert selected.enabled is False
    assert diagnostics["viable_candidates"] == 0


def test_policy_search_selects_profitable_calibration_thresholds():
    logits = _logits([[0.0, 0.0, 4.0]] * 20)
    returns = np.full((20, 1), 0.5, dtype=np.float32)
    timestamps = np.arange(20, dtype=np.int64) * 15 * 60

    selected, diagnostics = fit_probability_policy(
        logits,
        returns,
        timestamps,
        horizon_index=0,
        horizon_minutes=15,
        round_trip_cost_pct=0.15,
        confidence_thresholds=(0.50,),
        directional_margins=(0.10,),
        minimum_trades=5,
    )

    assert selected.enabled is True
    assert diagnostics["selected_metrics"]["trades"] == 20
    assert diagnostics["selected_metrics"]["average_trade_return_pct"] > 0
