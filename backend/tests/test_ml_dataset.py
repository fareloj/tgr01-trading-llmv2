import math

import pandas as pd
import pytest

from backend.ml.baselines import evaluate_baselines, evaluate_predictions, momentum_60
from backend.ml.dataset import (
    DatasetConfig,
    FEATURE_COLUMNS,
    build_market_dataset,
    chronological_split,
    select_labeled_horizon,
)
from backend.ml.readiness import assess_training_readiness


def candles(count: int = 900, *, slope: float = 0.04) -> pd.DataFrame:
    rows = []
    for index in range(count):
        close = 100.0 + slope * index + math.sin(index / 13.0) * 0.25
        open_price = close - math.sin(index / 7.0) * 0.05
        rows.append(
            {
                "timestamp": 1_800_000_000 + index * 60,
                "open": open_price,
                "high": max(open_price, close) + 0.15,
                "low": min(open_price, close) - 0.15,
                "close": close,
                "volume": 10.0 + (index % 17),
            }
        )
    return pd.DataFrame(rows)


def test_dataset_features_do_not_change_when_future_candles_change():
    source = candles()
    config = DatasetConfig()
    baseline = build_market_dataset(source, config)
    timestamp = int(baseline.iloc[50]["timestamp"])

    changed = source.copy()
    mask = changed["timestamp"] > timestamp
    changed.loc[mask, ["open", "high", "low", "close"]] *= 1.20
    rebuilt = build_market_dataset(changed, config)

    before = baseline.loc[baseline["timestamp"] == timestamp, list(FEATURE_COLUMNS)].reset_index(drop=True)
    after = rebuilt.loc[rebuilt["timestamp"] == timestamp, list(FEATURE_COLUMNS)].reset_index(drop=True)
    pd.testing.assert_frame_equal(before, after)


def test_dataset_requires_exact_future_timestamp_instead_of_bridging_gap():
    source = candles()
    target_index = 500
    decision_timestamp = int(source.iloc[target_index]["timestamp"])
    future_timestamp = decision_timestamp + 15 * 60
    source = source.loc[source["timestamp"] != future_timestamp].reset_index(drop=True)

    dataset = build_market_dataset(source, DatasetConfig(horizons_minutes=(15,), primary_horizon_minutes=15))

    assert decision_timestamp not in set(dataset["timestamp"])


def test_short_gaps_are_explicitly_filled_but_not_emitted_as_decisions():
    source = candles()
    missing_timestamp = int(source.iloc[500]["timestamp"])
    source = source.loc[source["timestamp"] != missing_timestamp].reset_index(drop=True)

    dataset = build_market_dataset(source)

    assert missing_timestamp not in set(dataset["timestamp"])
    assert dataset["observed_coverage_240"].min() < 1.0


def test_large_gaps_start_new_segments_and_require_fresh_history():
    first = candles(500)
    second = candles(500)
    second["timestamp"] += 24 * 60 * 60
    source = pd.concat([first, second], ignore_index=True)

    dataset = build_market_dataset(source)
    second_start = int(second["timestamp"].min())
    first_eligible_second_segment = int(dataset.loc[dataset["timestamp"] >= second_start, "timestamp"].min())

    assert first_eligible_second_segment >= second_start + 239 * 60


def test_dataset_rejects_inconsistent_ohlc():
    source = candles(300)
    source.loc[10, "high"] = source.loc[10, "low"] - 1.0

    with pytest.raises(ValueError, match="inconsistent OHLC"):
        build_market_dataset(source)


def test_dataset_config_rejects_history_shorter_than_longest_feature():
    with pytest.raises(ValueError, match="at least 240"):
        DatasetConfig(minimum_history_candles=100)


def test_dataset_rejects_timestamps_off_configured_grid():
    source = candles(300)
    source.loc[100, "timestamp"] += 1

    with pytest.raises(ValueError, match="not aligned"):
        build_market_dataset(source)


@pytest.mark.parametrize(
    ("slope", "expected"),
    [(0.10, "BUY"), (-0.10, "SELL"), (0.0, "HOLD")],
)
def test_labels_include_cost_and_minimum_edge(slope: float, expected: str):
    source = candles(slope=slope)
    if slope == 0.0:
        source[["open", "high", "low", "close"]] = [100.0, 100.1, 99.9, 100.0]
    config = DatasetConfig(
        horizons_minutes=(15,),
        primary_horizon_minutes=15,
        round_trip_cost_pct=0.10,
        minimum_net_edge_pct=0.05,
    )

    dataset = build_market_dataset(source, config)

    assert not dataset.empty
    assert set(dataset["label"]) == {expected}


def test_chronological_split_purges_targets_crossing_boundaries():
    dataset = build_market_dataset(candles())
    split = chronological_split(dataset, purge_minutes=60)

    validation_start = int(split.validation["timestamp"].min())
    test_start = int(split.test["timestamp"].min())
    assert int(split.train["timestamp"].max()) + 60 * 60 < validation_start
    assert int(split.validation["timestamp"].max()) + 60 * 60 < test_start
    assert split.sizes()["test"] > 0


def test_secondary_horizon_selection_requires_observed_future():
    source = candles()
    missing_future = int(source.iloc[700]["timestamp"])
    source = source.loc[source["timestamp"] != missing_future].reset_index(drop=True)
    dataset = build_market_dataset(
        source,
        DatasetConfig(horizons_minutes=(15, 60), primary_horizon_minutes=15),
    )

    selected = select_labeled_horizon(dataset, 60)

    assert selected["label_60m"].notna().all()
    assert selected["future_return_60m_pct"].notna().all()
    assert selected["future_observed_60m"].all()


def test_feature_allowlist_never_contains_future_or_label_columns():
    assert all("future" not in column and "label" not in column for column in FEATURE_COLUMNS)


def test_baseline_evaluation_skips_overlapping_trades():
    frame = build_market_dataset(candles(slope=0.10), DatasetConfig(horizons_minutes=(15,), primary_horizon_minutes=15))
    predicted = pd.Series("BUY", index=frame.index)

    metrics = evaluate_predictions(frame, predicted, horizon_minutes=15, round_trip_cost_pct=0.10)

    assert metrics["signal_count"] == len(frame)
    assert metrics["trade_count"] < metrics["signal_count"]
    assert metrics["overlap_skipped"] == metrics["signal_count"] - metrics["trade_count"]
    assert metrics["win_rate_after_cost"] == 1.0


def test_all_hold_accuracy_does_not_hide_zero_macro_recall_for_actions():
    source = candles(slope=0.0)
    source[["open", "high", "low", "close"]] = [100.0, 100.1, 99.9, 100.0]
    frame = build_market_dataset(source, DatasetConfig(horizons_minutes=(15,), primary_horizon_minutes=15))
    metrics = evaluate_baselines(frame, horizon_minutes=15, round_trip_cost_pct=0.15)["always_hold"]

    assert metrics["accuracy"] == 1.0
    assert metrics["macro_recall"] == pytest.approx(1 / 3, abs=1e-6)
    assert metrics["trade_count"] == 0


def test_momentum_baseline_has_only_supported_actions():
    frame = build_market_dataset(candles())

    assert set(momentum_60(frame).unique()).issubset({"BUY", "HOLD", "SELL"})


def test_prediction_evaluator_rejects_arbitrary_model_output():
    frame = build_market_dataset(candles()).head(10)
    prediction = pd.Series("RUN_SQL", index=frame.index)

    with pytest.raises(ValueError, match="unsupported actions"):
        evaluate_predictions(frame, prediction, horizon_minutes=15, round_trip_cost_pct=0.15)


def test_training_readiness_blocks_small_dataset():
    frame = build_market_dataset(candles())

    readiness = assess_training_readiness(frame, horizon_minutes=15)

    assert readiness["ready_for_model_experiments"] is False
    assert readiness["checks"]["minimum_rows"]["passed"] is False


def test_training_readiness_can_pass_explicit_test_thresholds():
    frame = build_market_dataset(candles())
    third = len(frame) // 3
    frame.loc[frame.index[:third], "label_15m"] = "BUY"
    frame.loc[frame.index[third : 2 * third], "label_15m"] = "HOLD"
    frame.loc[frame.index[2 * third :], "label_15m"] = "SELL"

    readiness = assess_training_readiness(
        frame,
        horizon_minutes=15,
        minimum_rows=100,
        minimum_calendar_days=1,
        minimum_rows_per_label=1,
        minimum_mean_coverage=0.50,
    )

    assert readiness["ready_for_model_experiments"] is True
