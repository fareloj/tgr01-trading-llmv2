from __future__ import annotations

import numpy as np
import pandas as pd

from backend.ml.dataset import FEATURE_COLUMNS
from backend.ml.slow_dataset import SLOW_TCN_FEATURE_COLUMNS, build_slow_tcn_dataset


def _market(rows: int, *, global_market: bool = False) -> pd.DataFrame:
    timestamps = np.arange(rows, dtype=np.int64) * 60 + 1_800_000_000
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "close": 100.0 + np.arange(rows) * (0.02 if global_market else 0.01),
            "is_observed": True,
        }
    )
    if not global_market:
        for index, column in enumerate(FEATURE_COLUMNS):
            frame[column] = np.full(rows, index / 100.0, dtype=np.float32)
    return frame


def test_slow_dataset_uses_closed_15_minute_rows_and_long_horizons():
    dataset = build_slow_tcn_dataset(
        _market(2_500),
        _market(2_500, global_market=True),
        cadence_minutes=15,
        horizons_minutes=(240, 1_440),
        actionable_move_pct=0.25,
    )

    assert (dataset["timestamp"] % (15 * 60) == 0).all()
    assert set(SLOW_TCN_FEATURE_COLUMNS).issubset(dataset.columns)
    valid_targets = dataset[["future_return_240m_pct", "future_return_1440m_pct"]].notna().all(axis=1)
    assert valid_targets.sum() > 50
    assert not valid_targets.iloc[-1]
    assert (dataset["timestamp"].diff().dropna() % (15 * 60) == 0).all()


def test_global_features_do_not_read_future_prices():
    local = _market(2_500)
    global_market = _market(2_500, global_market=True)
    before = build_slow_tcn_dataset(local, global_market)
    first_timestamp = int(before.iloc[0]["timestamp"])

    changed = global_market.copy()
    changed.loc[changed["timestamp"] > first_timestamp, "close"] *= 10.0
    after = build_slow_tcn_dataset(local, changed)

    first_before = before.loc[before["timestamp"] == first_timestamp]
    first_after = after.loc[after["timestamp"] == first_timestamp]
    np.testing.assert_allclose(
        first_before[[
            "global_return_15m_pct",
            "global_return_60m_pct",
            "global_return_240m_pct",
        ]],
        first_after[[
            "global_return_15m_pct",
            "global_return_60m_pct",
            "global_return_240m_pct",
        ]],
    )


def test_synthetic_context_does_not_break_sequence_or_become_stale_endpoint():
    local = _market(2_500)
    global_market = _market(2_500, global_market=True)
    local.loc[100:105, "is_observed"] = False
    local.loc[500:530, "is_observed"] = False

    dataset = build_slow_tcn_dataset(
        local,
        global_market,
        maximum_price_age_minutes=15,
    )

    assert (dataset["timestamp"].diff().dropna() % (15 * 60) == 0).all()
    stale_timestamp = int(local.iloc[525]["timestamp"] // (15 * 60) * (15 * 60))
    stale_row = dataset.loc[dataset["timestamp"] == stale_timestamp]
    if not stale_row.empty:
        assert not bool(stale_row.iloc[0]["is_observed"])
