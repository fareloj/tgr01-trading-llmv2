from __future__ import annotations

import copy
import math
import random
import time
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import torch
from torch.utils.data import DataLoader

from backend.ml.sequences import RobustTargetScaler
from backend.ml.tcn import QUANTILES, QuantileTCN, ordered_quantiles, quantile_loss


@dataclass(frozen=True)
class StageConfig:
    epochs: int
    learning_rate: float
    weight_decay: float = 1e-4
    patience: int = 2
    gradient_clip: float = 1.0

    def __post_init__(self) -> None:
        if self.epochs <= 0 or self.learning_rate <= 0 or self.patience <= 0:
            raise ValueError("training stage settings must be positive")
        if self.weight_decay < 0 or self.gradient_clip <= 0:
            raise ValueError("regularization settings are invalid")


def set_reproducible_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False


def _run_epoch(
    model: QuantileTCN,
    loader: DataLoader,
    *,
    device: torch.device,
    optimizer: torch.optim.Optimizer | None,
    scaler: torch.amp.GradScaler | None,
    target_scaler: RobustTargetScaler,
    gradient_clip: float,
) -> float:
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    total_rows = 0
    for features, targets in loader:
        features = features.to(device, non_blocking=True)
        targets = targets.to(device, non_blocking=True)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.autocast(
            device_type=device.type,
            dtype=torch.float16,
            enabled=device.type == "cuda",
        ):
            loss = quantile_loss(model(features), target_scaler.transform_tensor(targets))
        if training:
            assert optimizer is not None
            if scaler is not None:
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip)
                optimizer.step()
        rows = len(features)
        total_loss += float(loss.detach()) * rows
        total_rows += rows
    if total_rows == 0:
        raise ValueError("training loader yielded no rows")
    return total_loss / total_rows


def fit_stage(
    model: QuantileTCN,
    train_loader: DataLoader,
    validation_loader: DataLoader,
    *,
    device: torch.device,
    config: StageConfig,
    target_scaler: RobustTargetScaler,
) -> tuple[dict[str, torch.Tensor], list[dict[str, float]]]:
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    best_state = copy.deepcopy(model.state_dict())
    best_validation = math.inf
    stale_epochs = 0
    history = []
    for epoch in range(1, config.epochs + 1):
        started = time.perf_counter()
        train_loss = _run_epoch(
            model,
            train_loader,
            device=device,
            optimizer=optimizer,
            scaler=scaler if device.type == "cuda" else None,
            target_scaler=target_scaler,
            gradient_clip=config.gradient_clip,
        )
        with torch.inference_mode():
            validation_loss = _run_epoch(
                model,
                validation_loader,
                device=device,
                optimizer=None,
                scaler=None,
                target_scaler=target_scaler,
                gradient_clip=config.gradient_clip,
            )
        if not math.isfinite(train_loss) or not math.isfinite(validation_loss):
            raise RuntimeError("training produced a non-finite loss")
        row = {
            "epoch": float(epoch),
            "train_loss": train_loss,
            "validation_loss": validation_loss,
            "duration_seconds": time.perf_counter() - started,
        }
        history.append(row)
        print(
            f"epoch={epoch} train={train_loss:.6f} validation={validation_loss:.6f} "
            f"duration={row['duration_seconds']:.1f}s",
            flush=True,
        )
        if validation_loss < best_validation - 1e-7:
            best_validation = validation_loss
            best_state = copy.deepcopy(model.state_dict())
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= config.patience:
                break
    model.load_state_dict(best_state)
    return best_state, history


def predict_quantiles(
    model: QuantileTCN,
    loader: DataLoader,
    *,
    device: torch.device,
    target_scaler: RobustTargetScaler,
) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    predictions = []
    targets = []
    with torch.inference_mode():
        for features, batch_targets in loader:
            output = ordered_quantiles(
                target_scaler.inverse_tensor(model(features.to(device, non_blocking=True)))
            )
            predictions.append(output.cpu().numpy())
            targets.append(batch_targets.cpu().numpy())
    return np.concatenate(predictions), np.concatenate(targets)


def quantile_metrics(
    predictions: np.ndarray,
    targets: np.ndarray,
    horizons_minutes: Iterable[int],
) -> dict[str, dict[str, float]]:
    horizons = tuple(horizons_minutes)
    if predictions.shape != (len(targets), len(horizons), len(QUANTILES)):
        raise ValueError("prediction shape does not match targets and horizons")
    result = {}
    for index, horizon in enumerate(horizons):
        lower = predictions[:, index, 0]
        median = predictions[:, index, 1]
        upper = predictions[:, index, 2]
        actual = targets[:, index]
        mae = float(np.mean(np.abs(median - actual)))
        rmse = float(np.sqrt(np.mean(np.square(median - actual))))
        zero_mae = float(np.mean(np.abs(actual)))
        zero_rmse = float(np.sqrt(np.mean(np.square(actual))))
        result[f"{horizon}m"] = {
            "mae_pct": mae,
            "rmse_pct": rmse,
            "zero_return_mae_pct": zero_mae,
            "zero_return_rmse_pct": zero_rmse,
            "mae_improvement_over_zero": (zero_mae - mae) / zero_mae if zero_mae else 0.0,
            "median_direction_accuracy": float(np.mean(np.sign(median) == np.sign(actual))),
            "p10_p90_coverage": float(np.mean((actual >= lower) & (actual <= upper))),
            "mean_interval_width_pct": float(np.mean(upper - lower)),
        }
    return result
