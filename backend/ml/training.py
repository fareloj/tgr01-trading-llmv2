from __future__ import annotations

import copy
import math
import random
import time
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import torch
import torch.nn.functional as functional
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
    direction_loss_weight: float = 0.25
    actionable_move_pct: float = 0.20

    def __post_init__(self) -> None:
        if self.epochs <= 0 or self.learning_rate <= 0 or self.patience <= 0:
            raise ValueError("training stage settings must be positive")
        if self.weight_decay < 0 or self.gradient_clip <= 0:
            raise ValueError("regularization settings are invalid")
        if self.direction_loss_weight < 0 or self.actionable_move_pct <= 0:
            raise ValueError("multi-task loss settings are invalid")


def direction_classes(targets: torch.Tensor, actionable_move_pct: float) -> torch.Tensor:
    classes = torch.ones_like(targets, dtype=torch.long)
    classes[targets <= -actionable_move_pct] = 0
    classes[targets >= actionable_move_pct] = 2
    return classes


def fit_direction_class_weights(
    targets: np.ndarray,
    *,
    actionable_move_pct: float,
    weighting_power: float = 0.5,
    direction_targets: np.ndarray | None = None,
) -> torch.Tensor:
    if (
        targets.ndim != 2
        or len(targets) == 0
        or actionable_move_pct <= 0
        or not 0 <= weighting_power <= 1
    ):
        raise ValueError("direction class weight inputs are invalid")
    if direction_targets is None:
        tensor = torch.from_numpy(targets.astype(np.float32, copy=False))
        classes = direction_classes(tensor, actionable_move_pct)
    else:
        if direction_targets.shape != targets.shape or not np.isin(direction_targets, (0, 1, 2)).all():
            raise ValueError("direction targets must contain valid aligned classes")
        classes = torch.from_numpy(direction_targets.astype(np.int64, copy=False))
    weights = []
    for horizon in range(targets.shape[1]):
        counts = torch.bincount(classes[:, horizon], minlength=3).float()
        if torch.any(counts == 0):
            raise ValueError("each direction class must occur in training data")
        inverse_frequency = counts.pow(-weighting_power)
        weights.append(inverse_frequency / inverse_frequency.mean())
    return torch.stack(weights)


def direction_loss(
    logits: torch.Tensor,
    targets: torch.Tensor,
    *,
    actionable_move_pct: float,
    class_weights: torch.Tensor,
    direction_targets: torch.Tensor | None = None,
) -> torch.Tensor:
    if logits.shape != (*targets.shape, 3) or class_weights.shape != (targets.shape[1], 3):
        raise ValueError("direction logits, targets, or class weights have incompatible shapes")
    classes = (
        direction_classes(targets, actionable_move_pct)
        if direction_targets is None
        else direction_targets.long()
    )
    if classes.shape != targets.shape or torch.any((classes < 0) | (classes > 2)):
        raise ValueError("direction targets contain invalid classes")
    losses = [
        functional.cross_entropy(logits[:, index], classes[:, index], weight=class_weights[index])
        for index in range(targets.shape[1])
    ]
    return torch.stack(losses).mean()


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
    direction_class_weights: torch.Tensor,
    config: StageConfig,
    gradient_clip: float,
) -> float:
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    total_rows = 0
    for batch in loader:
        features, targets = batch[:2]
        batch_direction_targets = batch[2] if len(batch) == 3 else None
        features = features.to(device, non_blocking=True)
        targets = targets.to(device, non_blocking=True)
        if batch_direction_targets is not None:
            batch_direction_targets = batch_direction_targets.to(device, non_blocking=True)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.autocast(
            device_type=device.type,
            dtype=torch.float16,
            enabled=device.type == "cuda",
        ):
            quantiles, direction_logits = model.forward_heads(features)
            regression_loss = quantile_loss(quantiles, target_scaler.transform_tensor(targets))
            classification_loss = direction_loss(
                direction_logits,
                targets,
                actionable_move_pct=config.actionable_move_pct,
                class_weights=direction_class_weights,
                direction_targets=batch_direction_targets,
            )
            loss = regression_loss + config.direction_loss_weight * classification_loss
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
    direction_class_weights: torch.Tensor,
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
    direction_class_weights = direction_class_weights.to(device)
    for epoch in range(1, config.epochs + 1):
        started = time.perf_counter()
        train_loss = _run_epoch(
            model,
            train_loader,
            device=device,
            optimizer=optimizer,
            scaler=scaler if device.type == "cuda" else None,
            target_scaler=target_scaler,
            direction_class_weights=direction_class_weights,
            config=config,
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
                direction_class_weights=direction_class_weights,
                config=config,
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
        for batch in loader:
            features, batch_targets = batch[:2]
            output = ordered_quantiles(
                target_scaler.inverse_tensor(model(features.to(device, non_blocking=True)))
            )
            predictions.append(output.cpu().numpy())
            targets.append(batch_targets.cpu().numpy())
    return np.concatenate(predictions), np.concatenate(targets)


def predict_outputs(
    model: QuantileTCN,
    loader: DataLoader,
    *,
    device: torch.device,
    target_scaler: RobustTargetScaler,
    actionable_move_pct: float = 0.20,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    model.eval()
    predictions = []
    direction_logits = []
    targets = []
    direction_targets = []
    with torch.inference_mode():
        for batch in loader:
            features, batch_targets = batch[:2]
            batch_direction_targets = batch[2] if len(batch) == 3 else direction_classes(
                batch_targets,
                actionable_move_pct,
            )
            quantiles, logits = model.forward_heads(features.to(device, non_blocking=True))
            output = ordered_quantiles(target_scaler.inverse_tensor(quantiles))
            predictions.append(output.cpu().numpy())
            direction_logits.append(logits.cpu().numpy())
            targets.append(batch_targets.cpu().numpy())
            direction_targets.append(batch_direction_targets.cpu().numpy())
    return (
        np.concatenate(predictions),
        np.concatenate(direction_logits),
        np.concatenate(targets),
        np.concatenate(direction_targets),
    )


def direction_metrics(
    logits: np.ndarray,
    targets: np.ndarray,
    horizons_minutes: Iterable[int],
    *,
    actionable_move_pct: float,
    actual_classes: np.ndarray | None = None,
) -> dict[str, dict[str, float]]:
    horizons = tuple(horizons_minutes)
    if logits.shape != (len(targets), len(horizons), 3):
        raise ValueError("direction logits do not match targets and horizons")
    if actual_classes is None:
        actual = np.ones_like(targets, dtype=np.int8)
        actual[targets <= -actionable_move_pct] = 0
        actual[targets >= actionable_move_pct] = 2
    else:
        if actual_classes.shape != targets.shape or not np.isin(actual_classes, (0, 1, 2)).all():
            raise ValueError("actual direction classes are invalid")
        actual = actual_classes.astype(np.int8, copy=False)
    predicted = logits.argmax(axis=-1)
    shifted = logits - logits.max(axis=-1, keepdims=True)
    probabilities = np.exp(shifted)
    probabilities /= probabilities.sum(axis=-1, keepdims=True)
    result = {}
    for index, horizon in enumerate(horizons):
        recalls = []
        precisions = []
        f1_scores = []
        for class_index in range(3):
            truth = actual[:, index] == class_index
            chosen = predicted[:, index] == class_index
            true_positive = np.sum(truth & chosen)
            precision = true_positive / max(1, np.sum(chosen))
            recall = true_positive / max(1, np.sum(truth))
            recalls.append(recall)
            precisions.append(precision)
            f1_scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
        row_probabilities = probabilities[:, index]
        confidence = row_probabilities.max(axis=1)
        correctness = predicted[:, index] == actual[:, index]
        one_hot = np.eye(3, dtype=np.float64)[actual[:, index]]
        expected_calibration_error = 0.0
        for lower in np.linspace(0.0, 0.9, 10):
            upper = lower + 0.1
            mask = (confidence > lower) & (confidence <= upper)
            if mask.any():
                expected_calibration_error += float(mask.mean()) * abs(
                    float(correctness[mask].mean()) - float(confidence[mask].mean())
                )
        result[f"{horizon}m"] = {
            "accuracy": float(np.mean(predicted[:, index] == actual[:, index])),
            "balanced_accuracy": float(np.mean(recalls)),
            "macro_f1": float(np.mean(f1_scores)),
            "always_hold_accuracy": float(np.mean(actual[:, index] == 1)),
            "predicted_action_rate": float(np.mean(predicted[:, index] != 1)),
            "actual_action_rate": float(np.mean(actual[:, index] != 1)),
            "sell_precision": float(precisions[0]),
            "sell_recall": float(recalls[0]),
            "hold_precision": float(precisions[1]),
            "hold_recall": float(recalls[1]),
            "buy_precision": float(precisions[2]),
            "buy_recall": float(recalls[2]),
            "negative_log_likelihood": float(
                -np.log(np.clip(row_probabilities[np.arange(len(actual)), actual[:, index]], 1e-12, 1.0)).mean()
            ),
            "multiclass_brier": float(np.square(row_probabilities - one_hot).sum(axis=1).mean()),
            "expected_calibration_error": expected_calibration_error,
        }
    return result


def fit_direction_temperatures(
    logits: np.ndarray,
    actual_classes: np.ndarray,
) -> np.ndarray:
    if logits.ndim != 3 or logits.shape[:2] != actual_classes.shape or logits.shape[2] != 3:
        raise ValueError("temperature calibration arrays have incompatible shapes")
    if not np.isin(actual_classes, (0, 1, 2)).all():
        raise ValueError("temperature calibration classes are invalid")
    candidates = np.geomspace(0.25, 4.0, 121)
    temperatures = []
    rows = np.arange(len(logits))
    for horizon in range(logits.shape[1]):
        horizon_logits = logits[:, horizon]
        labels = actual_classes[:, horizon]
        losses = []
        for temperature in candidates:
            scaled = horizon_logits / temperature
            shifted = scaled - scaled.max(axis=1, keepdims=True)
            log_sum_exp = np.log(np.exp(shifted).sum(axis=1))
            losses.append(float(np.mean(log_sum_exp - shifted[rows, labels])))
        temperatures.append(float(candidates[int(np.argmin(losses))]))
    return np.asarray(temperatures, dtype=np.float32)


def apply_direction_temperatures(logits: np.ndarray, temperatures: np.ndarray) -> np.ndarray:
    if logits.ndim != 3 or temperatures.shape != (logits.shape[1],):
        raise ValueError("temperature scaling shapes are incompatible")
    if np.any(~np.isfinite(temperatures)) or np.any(temperatures <= 0):
        raise ValueError("temperatures must be finite and positive")
    return logits / temperatures.reshape(1, -1, 1)


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
