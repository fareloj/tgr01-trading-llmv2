from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from backend.ml.sequences import derive_continuous_segments


INVALID_DIRECTION = -1
SELL_DIRECTION = 0
HOLD_DIRECTION = 1
BUY_DIRECTION = 2


@dataclass(frozen=True)
class BarrierTargets:
    timestamps: np.ndarray
    labels: np.ndarray
    horizons_minutes: tuple[int, ...]
    barrier_pct: float

    def __post_init__(self) -> None:
        if self.timestamps.ndim != 1 or self.labels.shape != (
            len(self.timestamps),
            len(self.horizons_minutes),
        ):
            raise ValueError("barrier target arrays have incompatible shapes")
        if self.labels.dtype != np.int8:
            raise ValueError("barrier labels must use int8")
        if not np.isin(self.labels, (-1, 0, 1, 2)).all():
            raise ValueError("barrier labels contain an unsupported class")


def first_touch_barrier_targets(
    timestamps: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    is_observed: np.ndarray,
    horizons_minutes: Iterable[int],
    *,
    barrier_pct: float = 0.20,
    timeframe_seconds: int = 60,
) -> BarrierTargets:
    horizons = tuple(int(item) for item in horizons_minutes)
    rows = len(timestamps)
    if rows == 0 or any(array.shape != (rows,) for array in (high, low, close, is_observed)):
        raise ValueError("barrier inputs must be aligned non-empty vectors")
    if not horizons or any(item <= 0 for item in horizons):
        raise ValueError("barrier horizons must be positive")
    if barrier_pct <= 0 or timeframe_seconds <= 0:
        raise ValueError("barrier percentage and timeframe must be positive")
    if np.any(~np.isfinite(high)) or np.any(~np.isfinite(low)) or np.any(~np.isfinite(close)):
        raise ValueError("barrier prices must be finite")
    if np.any(close <= 0) or np.any(high < np.maximum(low, close)) or np.any(low > close):
        raise ValueError("barrier OHLC values are invalid")

    timestamps = timestamps.astype(np.int64, copy=False)
    observed = is_observed.astype(bool, copy=False)
    segments = derive_continuous_segments(timestamps, timeframe_seconds=timeframe_seconds)
    observed_prefix = np.concatenate(([0], np.cumsum(observed, dtype=np.int64)))
    labels = np.full((rows, len(horizons)), INVALID_DIRECTION, dtype=np.int8)
    barrier_fraction = barrier_pct / 100.0

    for horizon_index, horizon in enumerate(horizons):
        if horizon >= rows:
            continue
        starts = np.arange(rows - horizon, dtype=np.int64)
        future_ends = starts + horizon
        observed_future_count = observed_prefix[future_ends + 1] - observed_prefix[starts + 1]
        valid = (
            observed[starts]
            & (segments[starts] == segments[future_ends])
            & (observed_future_count == horizon)
        )
        unresolved = starts[valid]
        labels[unresolved, horizon_index] = HOLD_DIRECTION
        upper = close * (1.0 + barrier_fraction)
        lower = close * (1.0 - barrier_fraction)

        for step in range(1, horizon + 1):
            if len(unresolved) == 0:
                break
            future = unresolved + step
            touched_upper = high[future] >= upper[unresolved]
            touched_lower = low[future] <= lower[unresolved]
            ambiguous = touched_upper & touched_lower
            labels[unresolved[ambiguous], horizon_index] = INVALID_DIRECTION
            labels[unresolved[touched_upper & ~touched_lower], horizon_index] = BUY_DIRECTION
            labels[unresolved[touched_lower & ~touched_upper], horizon_index] = SELL_DIRECTION
            unresolved = unresolved[~(touched_upper | touched_lower)]

    return BarrierTargets(
        timestamps=timestamps.copy(),
        labels=labels,
        horizons_minutes=horizons,
        barrier_pct=float(barrier_pct),
    )
