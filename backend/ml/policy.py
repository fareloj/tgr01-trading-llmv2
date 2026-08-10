from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product

import numpy as np


@dataclass(frozen=True)
class ProbabilityPolicyConfig:
    """Convert calibrated direction probabilities into an abstaining advisory."""

    horizon_index: int
    horizon_minutes: int
    confidence_threshold: float
    directional_margin: float
    round_trip_cost_pct: float
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.horizon_index < 0 or self.horizon_minutes <= 0:
            raise ValueError("policy horizon is invalid")
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("confidence threshold must be between zero and one")
        if not 0.0 <= self.directional_margin <= 1.0:
            raise ValueError("directional margin must be between zero and one")
        if self.round_trip_cost_pct < 0:
            raise ValueError("round-trip cost must be non-negative")

    def as_dict(self) -> dict[str, int | float | bool]:
        return asdict(self)


def logits_to_probabilities(logits: np.ndarray) -> np.ndarray:
    if logits.ndim != 3 or logits.shape[2] != 3 or not np.isfinite(logits).all():
        raise ValueError("direction logits must have shape [rows, horizons, 3]")
    shifted = logits.astype(np.float64, copy=False) - logits.max(axis=2, keepdims=True)
    probabilities = np.exp(shifted)
    probabilities /= probabilities.sum(axis=2, keepdims=True)
    return probabilities


def probability_signals(
    logits: np.ndarray,
    config: ProbabilityPolicyConfig,
) -> np.ndarray:
    probabilities = logits_to_probabilities(logits)
    if config.horizon_index >= probabilities.shape[1]:
        raise ValueError("policy horizon index is outside model output")
    signals = np.zeros(len(probabilities), dtype=np.int8)
    if not config.enabled:
        return signals

    sell, hold, buy = probabilities[:, config.horizon_index].T
    strongest_other_for_buy = np.maximum(sell, hold)
    strongest_other_for_sell = np.maximum(buy, hold)
    buy_signal = (buy >= config.confidence_threshold) & (
        buy - strongest_other_for_buy >= config.directional_margin
    )
    sell_signal = (sell >= config.confidence_threshold) & (
        sell - strongest_other_for_sell >= config.directional_margin
    )
    signals[buy_signal] = 1
    signals[sell_signal] = -1
    return signals


def _non_overlapping_executions(
    signals: np.ndarray,
    timestamps: np.ndarray,
    horizon_minutes: int,
) -> np.ndarray:
    if signals.ndim != 1 or timestamps.shape != signals.shape:
        raise ValueError("signals and timestamps must be aligned vectors")
    if len(timestamps) and np.any(np.diff(timestamps) < 0):
        raise ValueError("timestamps must be ordered")
    executed = np.zeros(len(signals), dtype=bool)
    next_available = -1
    for index in np.flatnonzero(signals):
        timestamp = int(timestamps[index])
        if timestamp >= next_available:
            executed[index] = True
            next_available = timestamp + horizon_minutes * 60
    return executed


def evaluate_probability_policy(
    logits: np.ndarray,
    endpoint_returns_pct: np.ndarray,
    timestamps: np.ndarray,
    config: ProbabilityPolicyConfig,
    *,
    direction_targets: np.ndarray | None = None,
) -> dict[str, float | int | bool]:
    if endpoint_returns_pct.ndim != 2 or len(endpoint_returns_pct) != len(logits):
        raise ValueError("endpoint returns must align with direction logits")
    if config.horizon_index >= endpoint_returns_pct.shape[1]:
        raise ValueError("policy horizon index is outside endpoint returns")
    if not np.isfinite(endpoint_returns_pct[:, config.horizon_index]).all():
        raise ValueError("policy endpoint returns must be finite")
    if direction_targets is not None:
        if direction_targets.shape != endpoint_returns_pct.shape:
            raise ValueError("direction targets must align with endpoint returns")
        if not np.isin(direction_targets, (0, 1, 2)).all():
            raise ValueError("direction targets contain unsupported classes")

    signals = probability_signals(logits, config)
    executed = _non_overlapping_executions(signals, timestamps, config.horizon_minutes)
    returns = endpoint_returns_pct[:, config.horizon_index]
    trade_returns = signals[executed] * returns[executed] - config.round_trip_cost_pct
    equity = (
        np.cumprod(1.0 + trade_returns / 100.0)
        if len(trade_returns)
        else np.asarray([1.0], dtype=np.float64)
    )
    peaks = np.maximum.accumulate(equity)
    drawdown = (equity / peaks - 1.0) * 100.0
    result: dict[str, float | int | bool] = {
        "enabled": config.enabled,
        "rows": len(signals),
        "signals": int(np.count_nonzero(signals)),
        "trades": int(executed.sum()),
        "overlap_skipped": int(np.count_nonzero(signals) - executed.sum()),
        "abstention_rate": float(np.mean(signals == 0)) if len(signals) else 1.0,
        "win_rate_after_cost": float(np.mean(trade_returns > 0)) if len(trade_returns) else 0.0,
        "average_trade_return_pct": float(np.mean(trade_returns)) if len(trade_returns) else 0.0,
        "compounded_return_pct": float((equity[-1] - 1.0) * 100.0),
        "maximum_drawdown_pct": float(drawdown.min()),
    }
    if direction_targets is not None:
        predicted_classes = signals + 1
        actual_classes = direction_targets[:, config.horizon_index]
        result["barrier_direction_accuracy_on_trades"] = (
            float(np.mean(predicted_classes[executed] == actual_classes[executed]))
            if executed.any()
            else 0.0
        )
    return result


def fit_probability_policy(
    logits: np.ndarray,
    endpoint_returns_pct: np.ndarray,
    timestamps: np.ndarray,
    *,
    horizon_index: int,
    horizon_minutes: int,
    round_trip_cost_pct: float,
    direction_targets: np.ndarray | None = None,
    confidence_thresholds: tuple[float, ...] = (0.40, 0.45, 0.50, 0.55, 0.60),
    directional_margins: tuple[float, ...] = (0.00, 0.05, 0.10, 0.15, 0.20),
    minimum_trades: int = 10,
) -> tuple[ProbabilityPolicyConfig, dict[str, object]]:
    """Fit abstention thresholds on calibration data without touching test data."""

    if minimum_trades <= 0 or not confidence_thresholds or not directional_margins:
        raise ValueError("policy search settings are invalid")
    candidates: list[tuple[tuple[float, float, float, int], ProbabilityPolicyConfig, dict]] = []
    for confidence, margin in product(confidence_thresholds, directional_margins):
        config = ProbabilityPolicyConfig(
            horizon_index=horizon_index,
            horizon_minutes=horizon_minutes,
            confidence_threshold=float(confidence),
            directional_margin=float(margin),
            round_trip_cost_pct=round_trip_cost_pct,
        )
        metrics = evaluate_probability_policy(
            logits,
            endpoint_returns_pct,
            timestamps,
            config,
            direction_targets=direction_targets,
        )
        if int(metrics["trades"]) < minimum_trades:
            continue
        score = (
            float(metrics["compounded_return_pct"])
            + 0.50 * float(metrics["maximum_drawdown_pct"])
        )
        candidates.append(
            (
                (
                    score,
                    float(metrics["average_trade_return_pct"]),
                    float(metrics["win_rate_after_cost"]),
                    -int(metrics["trades"]),
                ),
                config,
                metrics,
            )
        )

    viable = [
        candidate
        for candidate in candidates
        if float(candidate[2]["compounded_return_pct"]) > 0.0
        and float(candidate[2]["average_trade_return_pct"]) > 0.0
    ]
    if viable:
        _, selected, selected_metrics = max(viable, key=lambda item: item[0])
    else:
        selected = ProbabilityPolicyConfig(
            horizon_index=horizon_index,
            horizon_minutes=horizon_minutes,
            confidence_threshold=1.0,
            directional_margin=1.0,
            round_trip_cost_pct=round_trip_cost_pct,
            enabled=False,
        )
        selected_metrics = evaluate_probability_policy(
            logits,
            endpoint_returns_pct,
            timestamps,
            selected,
            direction_targets=direction_targets,
        )

    diagnostics: dict[str, object] = {
        "selection_source": "calibration_only",
        "objective": "compounded_return_pct + 0.5 * maximum_drawdown_pct",
        "minimum_trades": minimum_trades,
        "searched_candidates": len(confidence_thresholds) * len(directional_margins),
        "eligible_candidates": len(candidates),
        "viable_candidates": len(viable),
        "selected_config": selected.as_dict(),
        "selected_metrics": selected_metrics,
    }
    return selected, diagnostics
