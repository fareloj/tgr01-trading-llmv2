from __future__ import annotations

import argparse
import gc
import json
import platform
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.ml.dataset import FEATURE_COLUMNS
from backend.ml.checkpoints import save_tcn_checkpoint
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
)
from backend.ml.tcn import QuantileTCN, TCNConfig
from backend.ml.training import (
    StageConfig,
    direction_metrics,
    fit_stage,
    fit_direction_class_weights,
    predict_outputs,
    predict_quantiles,
    quantile_metrics,
    set_reproducible_seed,
)


REPORTS_DIR = PROJECT_DIR / "backend" / "reports"
DEFAULT_GLOBAL = REPORTS_DIR / "binance_full_dataset.csv"
DEFAULT_LOCAL = REPORTS_DIR / "mb_tcn_dataset.csv"
DEFAULT_OUTPUT = REPORTS_DIR / "tcn"
HORIZONS = (15, 60)


def _cap_indices(indices: np.ndarray, maximum: int | None) -> np.ndarray:
    if maximum is None or len(indices) <= maximum:
        return indices
    if maximum <= 0:
        raise ValueError("maximum sequence count must be positive")
    positions = np.linspace(0, len(indices) - 1, num=maximum, dtype=np.int64)
    return indices[positions]


def _indices_for_range(
    data: ArrayMarketData,
    bounds: tuple[int, int],
    *,
    sequence_length: int,
    stride: int,
    maximum: int | None = None,
) -> np.ndarray:
    indices = continuous_end_indices(
        data.timestamps,
        data.segment_ids,
        start=bounds[0],
        end=bounds[1],
        sequence_length=sequence_length,
        stride=stride,
    )
    indices = observed_target_indices(data, indices)
    return _cap_indices(indices, maximum)


def _loader(
    data: ArrayMarketData,
    indices: np.ndarray,
    *,
    sequence_length: int,
    batch_size: int,
    shuffle: bool,
    device: torch.device,
    device_data: DeviceMarketData | None = None,
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
        drop_last=False,
    )


def _policy_metrics(
    predictions: np.ndarray,
    targets: np.ndarray,
    timestamps: np.ndarray,
    *,
    horizon_index: int,
    horizon_minutes: int,
    actionable_move_pct: float,
    round_trip_cost_pct: float,
) -> dict[str, float | int]:
    lower = predictions[:, horizon_index, 0]
    upper = predictions[:, horizon_index, 2]
    actual_return = targets[:, horizon_index]
    direction = np.zeros(len(predictions), dtype=np.int8)
    direction[lower > actionable_move_pct] = 1
    direction[upper < -actionable_move_pct] = -1
    actual_direction = np.zeros(len(targets), dtype=np.int8)
    actual_direction[actual_return >= actionable_move_pct] = 1
    actual_direction[actual_return <= -actionable_move_pct] = -1

    executed = np.zeros(len(direction), dtype=bool)
    next_available = -1
    for index in np.flatnonzero(direction):
        if int(timestamps[index]) >= next_available:
            executed[index] = True
            next_available = int(timestamps[index]) + horizon_minutes * 60
    trade_returns = direction[executed] * actual_return[executed] - round_trip_cost_pct
    equity = np.cumprod(1.0 + trade_returns / 100.0) if len(trade_returns) else np.array([1.0])
    peaks = np.maximum.accumulate(equity)
    drawdown = (equity / peaks - 1.0) * 100.0
    signaled = direction != 0
    return {
        "rows": len(direction),
        "signals": int(signaled.sum()),
        "trades": int(executed.sum()),
        "abstention_rate": float(1.0 - signaled.mean()),
        "action_accuracy": float(np.mean(direction == actual_direction)),
        "direction_accuracy_on_trades": (
            float(np.mean(direction[executed] == actual_direction[executed])) if executed.any() else 0.0
        ),
        "win_rate_after_cost": float(np.mean(trade_returns > 0)) if len(trade_returns) else 0.0,
        "average_trade_return_pct": float(np.mean(trade_returns)) if len(trade_returns) else 0.0,
        "compounded_return_pct": float((equity[-1] - 1.0) * 100.0),
        "maximum_drawdown_pct": float(drawdown.min()),
    }


def _prepare_domain(
    path: Path,
    *,
    scaler: RobustFeatureScaler | None,
    sequence_length: int,
    train_stride: int,
    evaluation_stride: int,
    maximum_sequences: int | None,
) -> tuple[ArrayMarketData, RobustFeatureScaler, object, dict[str, np.ndarray]]:
    data = load_market_arrays(path, HORIZONS)
    ranges = chronological_ranges(data.timestamps, purge_minutes=max(HORIZONS))
    if scaler is None:
        scaler = RobustFeatureScaler.fit(data.features[slice(*ranges.train)])
    data = replace(data, features=scaler.transform(data.features))
    indices = {
        "train": _indices_for_range(
            data,
            ranges.train,
            sequence_length=sequence_length,
            stride=train_stride,
            maximum=maximum_sequences,
        ),
        "validation": _indices_for_range(
            data,
            ranges.validation,
            sequence_length=sequence_length,
            stride=evaluation_stride,
            maximum=maximum_sequences,
        ),
        "test": _indices_for_range(
            data,
            ranges.test,
            sequence_length=sequence_length,
            stride=evaluation_stride,
            maximum=maximum_sequences,
        ),
    }
    return data, scaler, ranges, indices


def _fingerprint_dataset(path: Path) -> dict[str, object]:
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"dataset changed while it was being fingerprinted: {path}")
    return {
        "path": str(path),
        "size_bytes": before.st_size,
        "mtime_ns": before.st_mtime_ns,
        "sha256": digest,
    }


def _assert_dataset_unchanged(fingerprint: dict[str, object]) -> None:
    path = Path(str(fingerprint["path"]))
    current = path.stat()
    if (current.st_size, current.st_mtime_ns) != (
        int(fingerprint["size_bytes"]),
        int(fingerprint["mtime_ns"]),
    ):
        raise RuntimeError(f"dataset changed during training: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pretrain a causal quantile TCN globally and fine-tune it on BTC/BRL."
    )
    parser.add_argument("--global-dataset", default=str(DEFAULT_GLOBAL))
    parser.add_argument("--local-dataset", default=str(DEFAULT_LOCAL))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--sequence-length", type=int, default=240)
    parser.add_argument("--channels", type=int, default=32)
    parser.add_argument("--levels", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--global-stride", type=int, default=15)
    parser.add_argument("--local-stride", type=int, default=5)
    parser.add_argument("--global-epochs", type=int, default=3)
    parser.add_argument("--local-epochs", type=int, default=3)
    parser.add_argument("--global-learning-rate", type=float, default=1e-3)
    parser.add_argument("--local-learning-rate", type=float, default=2e-4)
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument("--maximum-sequences", type=int)
    parser.add_argument("--evaluate-test", action="store_true")
    parser.add_argument(
        "--host-loader",
        action="store_true",
        help="Keep sequence assembly on the CPU instead of caching features on CUDA.",
    )
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()

    if args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA was requested but torch.cuda.is_available() is false")
    device = torch.device(
        "cuda" if (args.device == "cuda" or args.device == "auto" and torch.cuda.is_available()) else "cpu"
    )
    set_reproducible_seed(args.seed)
    global_path = Path(args.global_dataset).resolve()
    local_path = Path(args.local_dataset).resolve()
    output_dir = Path(args.output_dir).resolve()
    tcn_config = TCNConfig(
        input_channels=len(FEATURE_COLUMNS),
        horizons_minutes=HORIZONS,
        channels=args.channels,
        levels=args.levels,
    )
    if tcn_config.receptive_field < args.sequence_length:
        raise SystemExit(
            f"TCN receptive field {tcn_config.receptive_field} is shorter than sequence "
            f"length {args.sequence_length}"
        )
    print(f"device={device} torch={torch.__version__} receptive_field={tcn_config.receptive_field}")

    global_fingerprint = _fingerprint_dataset(global_path)
    global_data, scaler, global_ranges, global_indices = _prepare_domain(
        global_path,
        scaler=None,
        sequence_length=args.sequence_length,
        train_stride=args.global_stride,
        evaluation_stride=args.global_stride,
        maximum_sequences=args.maximum_sequences,
    )
    print(f"global_sequences={dict((name, len(value)) for name, value in global_indices.items())}")
    target_scaler = RobustTargetScaler.fit(
        global_data.targets[global_indices["train"]],
        HORIZONS,
    )
    global_direction_weights = fit_direction_class_weights(
        global_data.targets[global_indices["train"]],
        actionable_move_pct=0.20,
    )
    global_device_data = (
        DeviceMarketData.from_arrays(global_data, device)
        if device.type == "cuda" and not args.host_loader
        else None
    )
    model = QuantileTCN(tcn_config).to(device)
    global_train = _loader(
        global_data,
        global_indices["train"],
        sequence_length=args.sequence_length,
        batch_size=args.batch_size,
        shuffle=True,
        device=device,
        device_data=global_device_data,
    )
    global_validation = _loader(
        global_data,
        global_indices["validation"],
        sequence_length=args.sequence_length,
        batch_size=args.batch_size,
        shuffle=False,
        device=device,
        device_data=global_device_data,
    )
    _, global_history = fit_stage(
        model,
        global_train,
        global_validation,
        device=device,
        config=StageConfig(
            epochs=args.global_epochs,
            learning_rate=args.global_learning_rate,
            patience=max(1, min(2, args.global_epochs)),
        ),
        target_scaler=target_scaler,
        direction_class_weights=global_direction_weights,
    )
    provenance = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "torch": str(torch.__version__),
        "cuda_runtime": str(torch.version.cuda) if torch.version.cuda else None,
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "seed": args.seed,
        "sequence_length": args.sequence_length,
        "tcn_config": tcn_config.as_dict(),
        "scaler": scaler.as_dict(),
        "target_scaler": target_scaler.as_dict(),
        "actionable_move_pct": 0.20,
        "global_direction_class_weights": global_direction_weights.tolist(),
        "global_dataset": global_fingerprint,
        "global_ranges": global_ranges.as_dict(),
        "global_sequences": {name: len(value) for name, value in global_indices.items()},
        "global_history": global_history,
    }
    _assert_dataset_unchanged(global_fingerprint)
    save_tcn_checkpoint(output_dir / "global_best.pt", model, provenance)
    del global_train, global_validation, global_device_data, global_data, global_indices
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()

    local_fingerprint = _fingerprint_dataset(local_path)
    local_data, _, local_ranges, local_indices = _prepare_domain(
        local_path,
        scaler=scaler,
        sequence_length=args.sequence_length,
        train_stride=args.local_stride,
        evaluation_stride=args.local_stride,
        maximum_sequences=args.maximum_sequences,
    )
    print(f"local_sequences={dict((name, len(value)) for name, value in local_indices.items())}")
    local_device_data = (
        DeviceMarketData.from_arrays(local_data, device)
        if device.type == "cuda" and not args.host_loader
        else None
    )
    local_train = _loader(
        local_data,
        local_indices["train"],
        sequence_length=args.sequence_length,
        batch_size=args.batch_size,
        shuffle=True,
        device=device,
        device_data=local_device_data,
    )
    local_direction_weights = fit_direction_class_weights(
        local_data.targets[local_indices["train"]],
        actionable_move_pct=0.20,
    )
    local_validation = _loader(
        local_data,
        local_indices["validation"],
        sequence_length=args.sequence_length,
        batch_size=args.batch_size,
        shuffle=False,
        device=device,
        device_data=local_device_data,
    )
    _, local_history = fit_stage(
        model,
        local_train,
        local_validation,
        device=device,
        config=StageConfig(
            epochs=args.local_epochs,
            learning_rate=args.local_learning_rate,
            patience=max(1, min(2, args.local_epochs)),
        ),
        target_scaler=target_scaler,
        direction_class_weights=local_direction_weights,
    )
    validation_predictions, validation_direction_logits, validation_targets = predict_outputs(
        model,
        local_validation,
        device=device,
        target_scaler=target_scaler,
    )
    validation_quantile_metrics = quantile_metrics(
        validation_predictions,
        validation_targets,
        HORIZONS,
    )
    validation_direction_metrics = direction_metrics(
        validation_direction_logits,
        validation_targets,
        HORIZONS,
        actionable_move_pct=0.20,
    )
    checkpoint_payload = {
        **provenance,
        "local_dataset": local_fingerprint,
        "local_ranges": local_ranges.as_dict(),
        "local_sequences": {name: len(value) for name, value in local_indices.items()},
        "local_history": local_history,
        "local_direction_class_weights": local_direction_weights.tolist(),
        "validation_quantile_metrics": validation_quantile_metrics,
        "validation_direction_metrics": validation_direction_metrics,
        "test_evaluated": bool(args.evaluate_test),
        "boundary": (
            "Offline research checkpoint only. It cannot place paper or live orders and remains "
            "subject to deterministic costs, persistence, freshness, exposure, and risk gates."
        ),
    }
    _assert_dataset_unchanged(local_fingerprint)
    report: dict[str, object] = {
        key: value for key, value in checkpoint_payload.items() if key != "scaler"
    }
    if args.evaluate_test:
        test_loader = _loader(
            local_data,
            local_indices["test"],
            sequence_length=args.sequence_length,
            batch_size=args.batch_size,
            shuffle=False,
            device=device,
            device_data=local_device_data,
        )
        predictions, targets = predict_quantiles(
            model,
            test_loader,
            device=device,
            target_scaler=target_scaler,
        )
        report["test_quantile_metrics"] = quantile_metrics(predictions, targets, HORIZONS)
        report["test_policy_15m"] = _policy_metrics(
            predictions,
            targets,
            local_data.timestamps[local_indices["test"]],
            horizon_index=0,
            horizon_minutes=15,
            actionable_move_pct=0.20,
            round_trip_cost_pct=0.15,
        )
    save_tcn_checkpoint(output_dir / "local_best.pt", model, checkpoint_payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "training_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "validation_quantile_metrics": validation_quantile_metrics,
                "validation_direction_metrics": validation_direction_metrics,
            },
            indent=2,
        )
    )
    print(json.dumps(report.get("test_quantile_metrics", {}), indent=2))
    print(json.dumps(report.get("test_policy_15m", {}), indent=2))
    print(f"checkpoint={output_dir / 'local_best.pt'}")
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
