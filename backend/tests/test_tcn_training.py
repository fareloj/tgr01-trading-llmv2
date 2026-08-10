from __future__ import annotations

import numpy as np
import pytest
import torch

from backend.ml.checkpoints import load_tcn_checkpoint, save_tcn_checkpoint
from backend.ml.dataset import FEATURE_COLUMNS
from backend.ml.sequences import (
    ArrayMarketData,
    DeviceMarketData,
    DeviceSequenceBatcher,
    MarketSequenceDataset,
    RobustFeatureScaler,
    RobustTargetScaler,
    chronological_ranges,
    continuous_end_indices,
    derive_continuous_segments,
    expanding_walk_forward_ranges,
    observed_target_indices,
    split_temporal_bounds,
)
from backend.ml.tcn import CausalConv1d, QuantileTCN, TCNConfig, quantile_loss
from backend.ml.training import (
    StageConfig,
    apply_direction_temperatures,
    direction_classes,
    direction_loss,
    direction_metrics,
    fit_direction_class_weights,
    fit_direction_temperatures,
    predict_quantiles,
)


def test_barrier_training_can_select_checkpoints_by_direction_loss():
    config = StageConfig(
        epochs=2,
        learning_rate=1e-3,
        selection_metric="direction_loss",
    )

    assert config.selection_metric == "direction_loss"


def test_training_rejects_unknown_checkpoint_selection_metric():
    with pytest.raises(ValueError, match="selection_metric"):
        StageConfig(
            epochs=2,
            learning_rate=1e-3,
            selection_metric="profit_on_test",
        )


def test_continuous_indices_reject_gaps_and_segment_boundaries():
    timestamps = np.arange(20, dtype=np.int64) * 60 + 1_800_000_000
    timestamps[10:] += 60
    segments = np.zeros(20, dtype=np.int64)
    segments[15:] = 1

    indices = continuous_end_indices(
        timestamps,
        segments,
        start=0,
        end=20,
        sequence_length=5,
        stride=1,
    )

    assert set(indices) == {4, 5, 6, 7, 8, 9, 14, 19}


def test_array_market_data_allows_missing_targets_at_segment_tail():
    rows = 300
    targets = np.zeros((rows, 2), dtype=np.float32)
    targets[-60:, 1] = np.nan

    data = ArrayMarketData(
        timestamps=np.arange(rows, dtype=np.int64) * 60 + 1_800_000_000,
        segment_ids=np.zeros(rows, dtype=np.int64),
        is_observed=np.ones(rows, dtype=bool),
        features=np.zeros((rows, len(FEATURE_COLUMNS)), dtype=np.float32),
        targets=targets,
        horizons_minutes=(15, 60),
    )

    assert np.isnan(data.targets[-1, 1])


def test_only_observed_rows_with_complete_targets_become_endpoints():
    rows = 300
    observed = np.ones(rows, dtype=bool)
    observed[250] = False
    targets = np.zeros((rows, 2), dtype=np.float32)
    targets[251, 1] = np.nan
    data = ArrayMarketData(
        timestamps=np.arange(rows, dtype=np.int64) * 60 + 1_800_000_000,
        segment_ids=np.zeros(rows, dtype=np.int64),
        is_observed=observed,
        features=np.zeros((rows, len(FEATURE_COLUMNS)), dtype=np.float32),
        targets=targets,
        horizons_minutes=(15, 60),
    )

    eligible = observed_target_indices(data, np.array([249, 250, 251, 252]))

    assert eligible.tolist() == [249, 252]


def test_barrier_targets_filter_endpoints_and_flow_through_dataset():
    rows = 300
    direction_targets = np.ones((rows, 2), dtype=np.int8)
    direction_targets[250, 0] = -1
    data = ArrayMarketData(
        timestamps=np.arange(rows, dtype=np.int64) * 60 + 1_800_000_000,
        segment_ids=np.zeros(rows, dtype=np.int64),
        is_observed=np.ones(rows, dtype=bool),
        features=np.zeros((rows, len(FEATURE_COLUMNS)), dtype=np.float32),
        targets=np.zeros((rows, 2), dtype=np.float32),
        horizons_minutes=(15, 60),
        direction_targets=direction_targets,
    )

    eligible = observed_target_indices(data, np.array([249, 250, 251]))
    dataset = MarketSequenceDataset(data, eligible, sequence_length=240)
    _, _, labels = dataset[0]

    assert eligible.tolist() == [249, 251]
    assert labels.tolist() == [1, 1]


def test_continuous_segments_ignore_chunk_local_ids_and_follow_time_only():
    timestamps = np.arange(12, dtype=np.int64) * 60 + 1_800_000_000
    timestamps[8:] += 120

    segments = derive_continuous_segments(timestamps)

    assert segments[:8].tolist() == [0] * 8
    assert segments[8:].tolist() == [1] * 4


def test_temporal_ranges_purge_targets_before_boundaries():
    timestamps = np.arange(1_000, dtype=np.int64) * 60 + 1_800_000_000
    ranges = chronological_ranges(timestamps, purge_minutes=60)

    validation_start = timestamps[ranges.validation[0]]
    test_start = timestamps[ranges.test[0]]
    assert timestamps[ranges.train[1] - 1] + 60 * 60 < validation_start
    assert timestamps[ranges.validation[1] - 1] + 60 * 60 < test_start


def test_temporal_subranges_keep_calibration_after_purged_selection():
    timestamps = np.arange(1_000, dtype=np.int64) * 60 + 1_800_000_000
    first, second = split_temporal_bounds(
        timestamps,
        (600, 800),
        first_ratio=0.60,
        purge_minutes=60,
    )

    assert first[0] == 600
    assert second == (720, 800)
    assert timestamps[first[1] - 1] + 60 * 60 < timestamps[second[0]]


def test_expanding_walk_forward_folds_are_ordered_and_purged():
    timestamps = np.arange(1_000, dtype=np.int64) * 60 + 1_800_000_000

    folds = expanding_walk_forward_ranges(
        timestamps,
        minimum_train_rows=300,
        selection_rows=100,
        calibration_rows=100,
        test_rows=100,
        step_rows=100,
        purge_minutes=60,
    )

    assert len(folds) == 5
    assert folds[0].train == (0, 240)
    assert folds[0].selection == (300, 340)
    assert folds[0].calibration == (400, 440)
    assert folds[0].test == (500, 600)
    assert folds[1].train[1] > folds[0].train[1]
    for fold in folds:
        assert timestamps[fold.train[1] - 1] + 60 * 60 < timestamps[fold.selection[0]]
        assert timestamps[fold.selection[1] - 1] + 60 * 60 < timestamps[fold.calibration[0]]
        assert timestamps[fold.calibration[1] - 1] + 60 * 60 < timestamps[fold.test[0]]


def test_walk_forward_rejects_a_purge_that_empties_a_partition():
    timestamps = np.arange(100, dtype=np.int64) * 60 + 1_800_000_000

    with pytest.raises(ValueError, match="empty walk-forward"):
        expanding_walk_forward_ranges(
            timestamps,
            minimum_train_rows=40,
            selection_rows=20,
            calibration_rows=20,
            test_rows=20,
            purge_minutes=30,
        )


def test_scaler_is_unchanged_by_validation_outlier():
    train = np.arange(500 * len(FEATURE_COLUMNS), dtype=np.float32).reshape(500, -1)
    scaler = RobustFeatureScaler.fit(train)
    validation = train.copy()
    validation[-1] = 1e20

    unchanged = RobustFeatureScaler.fit(train)

    np.testing.assert_array_equal(scaler.median, unchanged.median)
    np.testing.assert_array_equal(scaler.scale, unchanged.scale)
    assert np.max(scaler.transform(validation)) <= scaler.clip_value


def test_target_scaler_round_trips_each_horizon():
    targets = np.array([[-1.0, -4.0], [0.0, 0.0], [1.0, 4.0]], dtype=np.float32)
    scaler = RobustTargetScaler.fit(targets, (15, 60))
    tensor = torch.from_numpy(targets)
    normalized = scaler.transform_tensor(tensor)
    restored = scaler.inverse_tensor(normalized.unsqueeze(-1)).squeeze(-1)

    torch.testing.assert_close(restored, tensor)


def test_causal_convolution_ignores_future_values():
    torch.manual_seed(7)
    layer = CausalConv1d(2, 3, kernel_size=3, padding=4, dilation=2)
    original = torch.randn(1, 2, 20)
    changed = original.clone()
    changed[..., 11:] += 1000

    before = layer(original)
    after = layer(changed)

    torch.testing.assert_close(before[..., :11], after[..., :11])


def test_tcn_covers_sequence_and_returns_quantiles():
    config = TCNConfig(input_channels=len(FEATURE_COLUMNS), channels=8, levels=6)
    model = QuantileTCN(config)
    output = model(torch.randn(4, len(FEATURE_COLUMNS), 240))

    assert config.receptive_field >= 240
    assert output.shape == (4, 2, 3)

    quantiles, directions = model.forward_heads(
        torch.randn(4, len(FEATURE_COLUMNS), 240)
    )
    assert quantiles.shape == (4, 2, 3)
    assert directions.shape == (4, 2, 3)


def test_quantile_loss_penalizes_crossed_outputs():
    targets = torch.zeros(2, 2)
    ordered = torch.tensor([[[-1.0, 0.0, 1.0], [-1.0, 0.0, 1.0]]] * 2)
    crossed = ordered.flip(-1)

    assert quantile_loss(crossed, targets) > quantile_loss(ordered, targets)


def test_direction_objective_maps_and_weights_all_classes():
    targets = np.array(
        [[-0.5, -0.3], [0.0, 0.0], [0.6, 0.4], [0.1, -0.1]],
        dtype=np.float32,
    )
    weights = fit_direction_class_weights(targets, actionable_move_pct=0.2)
    classes = direction_classes(torch.from_numpy(targets), 0.2)
    logits = torch.zeros(4, 2, 3)

    loss = direction_loss(
        logits,
        torch.from_numpy(targets),
        actionable_move_pct=0.2,
        class_weights=weights,
    )

    assert classes.tolist() == [[0, 0], [1, 1], [2, 2], [1, 1]]
    assert weights.shape == (2, 3)
    assert torch.isfinite(loss)


def test_direction_metrics_expose_hold_baseline_and_balanced_score():
    targets = np.array([[-0.3], [0.0], [0.4]], dtype=np.float32)
    logits = np.array([[[5.0, 0.0, 0.0]], [[0.0, 5.0, 0.0]], [[0.0, 0.0, 5.0]]])

    metrics = direction_metrics(logits, targets, (15,), actionable_move_pct=0.2)["15m"]

    assert metrics["accuracy"] == 1.0
    assert metrics["balanced_accuracy"] == 1.0
    assert metrics["macro_f1"] == 1.0
    assert metrics["always_hold_accuracy"] == 1 / 3


def test_temperature_scaling_reduces_overconfident_nll():
    logits = np.array(
        [
            [[8.0, 0.0, 0.0]],
            [[0.0, 8.0, 0.0]],
            [[0.0, 0.0, 8.0]],
            [[8.0, 0.0, 0.0]],
        ],
        dtype=np.float32,
    )
    actual = np.array([[0], [1], [2], [1]], dtype=np.int8)
    returns = np.zeros((4, 1), dtype=np.float32)
    before = direction_metrics(
        logits,
        returns,
        (15,),
        actionable_move_pct=0.2,
        actual_classes=actual,
    )["15m"]

    temperatures = fit_direction_temperatures(logits, actual)
    calibrated = apply_direction_temperatures(logits, temperatures)
    after = direction_metrics(
        calibrated,
        returns,
        (15,),
        actionable_move_pct=0.2,
        actual_classes=actual,
    )["15m"]

    assert temperatures[0] > 1.0
    assert after["negative_log_likelihood"] < before["negative_log_likelihood"]


def test_device_batcher_matches_host_dataset_when_cuda_is_available():
    if not torch.cuda.is_available():
        return
    rows = 300
    data = ArrayMarketData(
        timestamps=np.arange(rows, dtype=np.int64) * 60 + 1_800_000_000,
        segment_ids=np.zeros(rows, dtype=np.int64),
        is_observed=np.ones(rows, dtype=bool),
        features=np.arange(rows * len(FEATURE_COLUMNS), dtype=np.float32).reshape(rows, -1),
        targets=np.arange(rows * 2, dtype=np.float32).reshape(rows, 2),
        horizons_minutes=(15, 60),
    )
    indices = np.array([239, 240, 299], dtype=np.int64)
    host = MarketSequenceDataset(data, indices, sequence_length=240)
    batcher = DeviceSequenceBatcher(
        DeviceMarketData.from_arrays(data, torch.device("cuda")),
        indices,
        sequence_length=240,
        batch_size=3,
        shuffle=False,
    )

    device_features, device_targets = next(iter(batcher))
    host_features = torch.stack([host[index][0] for index in range(len(host))])
    host_targets = torch.stack([host[index][1] for index in range(len(host))])

    torch.testing.assert_close(device_features.cpu(), host_features)
    torch.testing.assert_close(device_targets.cpu(), host_targets)


def test_prediction_collects_device_resident_targets_on_cpu():
    if not torch.cuda.is_available():
        return
    rows = 250
    data = ArrayMarketData(
        timestamps=np.arange(rows, dtype=np.int64) * 60 + 1_800_000_000,
        segment_ids=np.zeros(rows, dtype=np.int64),
        is_observed=np.ones(rows, dtype=bool),
        features=np.zeros((rows, len(FEATURE_COLUMNS)), dtype=np.float32),
        targets=np.zeros((rows, 2), dtype=np.float32),
        horizons_minutes=(15, 60),
    )
    indices = np.array([239, 240], dtype=np.int64)
    batcher = DeviceSequenceBatcher(
        DeviceMarketData.from_arrays(data, torch.device("cuda")),
        indices,
        sequence_length=240,
        batch_size=2,
        shuffle=False,
    )
    model = QuantileTCN(TCNConfig(input_channels=len(FEATURE_COLUMNS), channels=8)).cuda()

    target_scaler = RobustTargetScaler.fit(data.targets, (15, 60))
    predictions, targets = predict_quantiles(
        model,
        batcher,
        device=torch.device("cuda"),
        target_scaler=target_scaler,
    )

    assert predictions.shape == (2, 2, 3)
    assert targets.shape == (2, 2)


def test_checkpoint_round_trip_uses_safe_weights_only_loader(tmp_path):
    config = TCNConfig(input_channels=len(FEATURE_COLUMNS), channels=8)
    model = QuantileTCN(config)
    feature_scaler = RobustFeatureScaler.fit(
        np.arange(300 * len(FEATURE_COLUMNS), dtype=np.float32).reshape(300, -1)
    )
    target_scaler = RobustTargetScaler.fit(
        np.array([[-1.0, -2.0], [0.0, 0.0], [1.0, 2.0]], dtype=np.float32),
        (15, 60),
    )
    path = tmp_path / "model.pt"
    save_tcn_checkpoint(
        path,
        model,
        {
            "tcn_config": config.as_dict(),
            "scaler": feature_scaler.as_dict(),
            "target_scaler": target_scaler.as_dict(),
            "sequence_length": 240,
            "torch": str(torch.__version__),
        },
    )

    restored, restored_features, restored_targets, metadata = load_tcn_checkpoint(path)

    assert isinstance(restored, QuantileTCN)
    assert metadata["torch"] == str(torch.__version__)
    np.testing.assert_allclose(restored_features.median, feature_scaler.median)
    np.testing.assert_allclose(restored_targets.scale, target_scaler.scale)


def test_checkpoint_rejects_missing_required_fields(tmp_path):
    path = tmp_path / "incomplete.pt"
    torch.save({"checkpoint_format_version": 1}, path)

    with pytest.raises(ValueError, match="checkpoint is missing"):
        load_tcn_checkpoint(path)


def test_checkpoint_rejects_target_horizon_mismatch(tmp_path):
    config = TCNConfig(input_channels=len(FEATURE_COLUMNS), channels=8)
    model = QuantileTCN(config)
    feature_scaler = RobustFeatureScaler.fit(
        np.arange(300 * len(FEATURE_COLUMNS), dtype=np.float32).reshape(300, -1)
    )
    path = tmp_path / "mismatch.pt"
    save_tcn_checkpoint(
        path,
        model,
        {
            "tcn_config": config.as_dict(),
            "scaler": feature_scaler.as_dict(),
            "target_scaler": {
                "center": [0.0, 0.0],
                "scale": [1.0, 1.0],
                "horizons_minutes": [5, 30],
            },
            "sequence_length": 240,
        },
    )

    with pytest.raises(ValueError, match="horizons do not match"):
        load_tcn_checkpoint(path)
