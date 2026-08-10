from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.ml.checkpoints import save_tcn_checkpoint
from backend.ml.policy import evaluate_probability_policy, fit_probability_policy
from backend.ml.sequences import (
    ArrayMarketData,
    DeviceMarketData,
    DeviceSequenceBatcher,
    MarketSequenceDataset,
    RobustFeatureScaler,
    RobustTargetScaler,
    chronological_ranges,
    continuous_end_indices,
    load_market_arrays,
    observed_target_indices,
    sha256_file,
    split_temporal_bounds,
)
from backend.ml.slow_dataset import SLOW_TCN_FEATURE_COLUMNS
from backend.ml.tcn import QuantileTCN, TCNConfig
from backend.ml.training import (
    StageConfig,
    apply_direction_temperatures,
    direction_metrics,
    fit_direction_class_weights,
    fit_direction_temperatures,
    fit_stage,
    predict_outputs,
    quantile_metrics,
    set_reproducible_seed,
)


REPORTS_DIR = PROJECT_DIR / "backend" / "reports"
DEFAULT_DATASET = REPORTS_DIR / "mb_slow_tcn_v2.csv"
DEFAULT_OUTPUT = REPORTS_DIR / "tcn_slow_v2"
HORIZONS = (240, 1_440)
TIMEFRAME_SECONDS = 15 * 60
ACTIONABLE_MOVE_PCT = 0.25
ROUND_TRIP_COST_PCT = 0.20


def _indices(
    data: ArrayMarketData,
    bounds: tuple[int, int],
    *,
    sequence_length: int,
    stride: int,
    maximum: int | None,
) -> np.ndarray:
    values = continuous_end_indices(
        data.timestamps,
        data.segment_ids,
        start=bounds[0],
        end=bounds[1],
        sequence_length=sequence_length,
        stride=stride,
        timeframe_seconds=TIMEFRAME_SECONDS,
    )
    values = observed_target_indices(data, values)
    if maximum is not None and len(values) > maximum:
        positions = np.linspace(0, len(values) - 1, num=maximum, dtype=np.int64)
        values = values[positions]
    if len(values) == 0:
        raise ValueError("temporal partition contains no eligible slow TCN sequences")
    return values


def _loader(
    data: ArrayMarketData,
    indices: np.ndarray,
    *,
    sequence_length: int,
    batch_size: int,
    shuffle: bool,
    device: torch.device,
    device_data: DeviceMarketData | None,
):
    if device_data is not None:
        return DeviceSequenceBatcher(
            device_data,
            indices,
            sequence_length=sequence_length,
            batch_size=batch_size,
            shuffle=shuffle,
        )
    return DataLoader(
        MarketSequenceDataset(data, indices, sequence_length=sequence_length),
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=0,
        pin_memory=device.type == "cuda",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train the isolated 15-minute, 4h/24h TCN v2 research model."
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--sequence-length", type=int, default=48)
    parser.add_argument("--channels", type=int, default=32)
    parser.add_argument("--levels", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--direction-loss-weight", type=float, default=1.0)
    parser.add_argument("--class-weight-power", type=float, default=0.5)
    parser.add_argument("--train-stride", type=int, default=1)
    parser.add_argument("--evaluation-stride", type=int, default=1)
    parser.add_argument("--maximum-sequences", type=int)
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument("--evaluate-test", action="store_true")
    parser.add_argument("--host-loader", action="store_true")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()
    if (
        args.sequence_length <= 0
        or args.batch_size <= 0
        or args.epochs <= 0
        or args.learning_rate <= 0
        or args.direction_loss_weight < 0
        or not 0 <= args.class_weight_power <= 1
        or args.train_stride <= 0
        or args.evaluation_stride <= 0
    ):
        parser.error("training arguments are invalid")
    if args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA was requested but torch.cuda.is_available() is false")
    device = torch.device(
        "cuda" if args.device == "cuda" or args.device == "auto" and torch.cuda.is_available() else "cpu"
    )
    set_reproducible_seed(args.seed)

    dataset_path = Path(args.dataset).resolve()
    output_dir = Path(args.output_dir).resolve()
    dataset_hash = sha256_file(dataset_path)
    data = load_market_arrays(
        dataset_path,
        HORIZONS,
        feature_columns=SLOW_TCN_FEATURE_COLUMNS,
        timeframe_seconds=TIMEFRAME_SECONDS,
    )
    ranges = chronological_ranges(data.timestamps, purge_minutes=max(HORIZONS))
    selection_bounds, calibration_bounds = split_temporal_bounds(
        data.timestamps,
        ranges.validation,
        first_ratio=0.60,
        purge_minutes=max(HORIZONS),
    )
    bounds = {
        "train": ranges.train,
        "selection": selection_bounds,
        "calibration": calibration_bounds,
        "test": ranges.test,
    }
    indices = {
        name: _indices(
            data,
            partition,
            sequence_length=args.sequence_length,
            stride=args.train_stride if name == "train" else args.evaluation_stride,
            maximum=args.maximum_sequences,
        )
        for name, partition in bounds.items()
    }
    feature_scaler = RobustFeatureScaler.fit(
        data.features[indices["train"]],
        feature_columns=SLOW_TCN_FEATURE_COLUMNS,
    )
    data = replace(data, features=feature_scaler.transform(data.features))
    target_scaler = RobustTargetScaler.fit(data.targets[indices["train"]], HORIZONS)
    class_weights = fit_direction_class_weights(
        data.targets[indices["train"]],
        actionable_move_pct=ACTIONABLE_MOVE_PCT,
        weighting_power=args.class_weight_power,
    )
    config = TCNConfig(
        input_channels=len(SLOW_TCN_FEATURE_COLUMNS),
        horizons_minutes=HORIZONS,
        channels=args.channels,
        levels=args.levels,
    )
    if config.receptive_field < args.sequence_length:
        raise SystemExit(
            f"TCN receptive field {config.receptive_field} is shorter than sequence "
            f"length {args.sequence_length}"
        )
    model = QuantileTCN(config).to(device)
    device_data = (
        DeviceMarketData.from_arrays(data, device)
        if device.type == "cuda" and not args.host_loader
        else None
    )
    loaders = {
        name: _loader(
            data,
            values,
            sequence_length=args.sequence_length,
            batch_size=args.batch_size,
            shuffle=name == "train",
            device=device,
            device_data=device_data,
        )
        for name, values in indices.items()
    }
    print(
        f"device={device} rows={len(data.timestamps)} sequences="
        f"{dict((name, len(value)) for name, value in indices.items())}"
    )
    _, history = fit_stage(
        model,
        loaders["train"],
        loaders["selection"],
        device=device,
        config=StageConfig(
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            patience=min(3, args.epochs),
            direction_loss_weight=args.direction_loss_weight,
            actionable_move_pct=ACTIONABLE_MOVE_PCT,
            selection_metric="direction_loss",
        ),
        target_scaler=target_scaler,
        direction_class_weights=class_weights,
    )
    selection_outputs = predict_outputs(
        model,
        loaders["selection"],
        device=device,
        target_scaler=target_scaler,
        actionable_move_pct=ACTIONABLE_MOVE_PCT,
    )
    temperatures = fit_direction_temperatures(selection_outputs[1], selection_outputs[3])
    calibration_outputs = predict_outputs(
        model,
        loaders["calibration"],
        device=device,
        target_scaler=target_scaler,
        actionable_move_pct=ACTIONABLE_MOVE_PCT,
    )
    calibrated_logits = apply_direction_temperatures(calibration_outputs[1], temperatures)
    probability_policy, policy_calibration = fit_probability_policy(
        calibrated_logits,
        calibration_outputs[2],
        data.timestamps[indices["calibration"]],
        horizon_index=0,
        horizon_minutes=HORIZONS[0],
        round_trip_cost_pct=ROUND_TRIP_COST_PCT,
        direction_targets=calibration_outputs[3],
        minimum_trades=10,
    )
    calibration_metrics = {
        "quantiles": quantile_metrics(calibration_outputs[0], calibration_outputs[2], HORIZONS),
        "direction": direction_metrics(
            calibrated_logits,
            calibration_outputs[2],
            HORIZONS,
            actionable_move_pct=ACTIONABLE_MOVE_PCT,
            actual_classes=calibration_outputs[3],
        ),
        "policy_4h": policy_calibration,
    }
    metadata: dict[str, object] = {
        "experiment": "slow_tcn_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "torch": str(torch.__version__),
        "cuda_runtime": str(torch.version.cuda) if torch.version.cuda else None,
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "seed": args.seed,
        "dataset": {"path": str(dataset_path), "sha256": dataset_hash},
        "sequence_length": args.sequence_length,
        "timeframe_seconds": TIMEFRAME_SECONDS,
        "tcn_config": config.as_dict(),
        "scaler": feature_scaler.as_dict(),
        "target_scaler": target_scaler.as_dict(),
        "actionable_move_pct": ACTIONABLE_MOVE_PCT,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "direction_target_mode": "endpoint",
        "direction_temperatures": temperatures.tolist(),
        "ranges": {name: list(value) for name, value in bounds.items()},
        "sequences": {name: len(value) for name, value in indices.items()},
        "history": history,
        "calibration_metrics": calibration_metrics,
        "probability_policy": probability_policy.as_dict(),
        "test_evaluated": bool(args.evaluate_test),
        "execution_eligible": False,
        "can_authorize_order": False,
        "boundary": "Offline research only; excluded from LLM and execution paths.",
    }
    report = dict(metadata)
    if args.evaluate_test:
        test_outputs = predict_outputs(
            model,
            loaders["test"],
            device=device,
            target_scaler=target_scaler,
            actionable_move_pct=ACTIONABLE_MOVE_PCT,
        )
        test_logits = apply_direction_temperatures(test_outputs[1], temperatures)
        report["test_metrics"] = {
            "quantiles": quantile_metrics(test_outputs[0], test_outputs[2], HORIZONS),
            "direction": direction_metrics(
                test_logits,
                test_outputs[2],
                HORIZONS,
                actionable_move_pct=ACTIONABLE_MOVE_PCT,
                actual_classes=test_outputs[3],
            ),
            "policy_4h": evaluate_probability_policy(
                test_logits,
                test_outputs[2],
                data.timestamps[indices["test"]],
                probability_policy,
                direction_targets=test_outputs[3],
            ),
        }
        metadata["test_metrics"] = report["test_metrics"]

    output_dir.mkdir(parents=True, exist_ok=True)
    save_tcn_checkpoint(output_dir / "model.pt", model, metadata)
    report_path = output_dir / "training_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(calibration_metrics, indent=2))
    print(f"checkpoint={output_dir / 'model.pt'}")
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
