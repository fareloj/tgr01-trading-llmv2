from __future__ import annotations

import numpy as np

from backend.ml.barriers import (
    BUY_DIRECTION,
    HOLD_DIRECTION,
    INVALID_DIRECTION,
    SELL_DIRECTION,
    first_touch_barrier_targets,
)


def _series(rows: int = 12):
    timestamps = np.arange(rows, dtype=np.int64) * 60 + 1_800_000_000
    close = np.full(rows, 100.0)
    high = close.copy()
    low = close.copy()
    observed = np.ones(rows, dtype=bool)
    return timestamps, high, low, close, observed


def test_first_touch_labels_buy_sell_hold_and_ambiguous():
    timestamps, high, low, close, observed = _series()
    high[1] = 101.0
    low[4] = 99.0
    high[7] = 101.0
    low[7] = 99.0

    targets = first_touch_barrier_targets(
        timestamps,
        high,
        low,
        close,
        observed,
        (3,),
        barrier_pct=0.5,
    )

    assert targets.labels[0, 0] == BUY_DIRECTION
    assert targets.labels[2, 0] == SELL_DIRECTION
    assert targets.labels[8, 0] == HOLD_DIRECTION
    assert targets.labels[6, 0] == INVALID_DIRECTION


def test_first_touch_rejects_windows_with_missing_or_discontinuous_future():
    timestamps, high, low, close, observed = _series()
    observed[2] = False
    timestamps[8:] += 60

    targets = first_touch_barrier_targets(
        timestamps,
        high,
        low,
        close,
        observed,
        (3,),
        barrier_pct=0.5,
    )

    assert targets.labels[0, 0] == INVALID_DIRECTION
    assert targets.labels[6, 0] == INVALID_DIRECTION
    assert targets.labels[8, 0] == HOLD_DIRECTION
