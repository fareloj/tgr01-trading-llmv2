from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from backend.ml.dataset import FEATURE_COLUMNS


SLOW_TCN_FEATURE_COLUMNS = (
    *FEATURE_COLUMNS,
    "utc_time_sin",
    "utc_time_cos",
    "utc_weekday_sin",
    "utc_weekday_cos",
    "global_return_15m_pct",
    "global_return_60m_pct",
    "global_return_240m_pct",
    "cross_market_return_gap_60m_pct",
)


def _observed(values: pd.Series) -> pd.Series:
    if values.dtype == object:
        normalized = values.astype(str).str.strip().str.lower()
        converted = normalized.map({"true": True, "false": False, "1": True, "0": False})
        if converted.isna().any():
            raise ValueError("is_observed contains unsupported values")
        return converted.astype(bool)
    return values.fillna(False).astype(bool)


def _validated_market_frame(frame: pd.DataFrame, *, require_features: bool) -> pd.DataFrame:
    required = {"timestamp", "close", "is_observed"}
    if require_features:
        required.update(FEATURE_COLUMNS)
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"market frame is missing columns: {', '.join(sorted(missing))}")
    result = frame.copy()
    result["timestamp"] = pd.to_numeric(result["timestamp"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    if result[["timestamp", "close"]].isna().any().any() or (result["close"] <= 0).any():
        raise ValueError("market frame contains invalid timestamps or closes")
    result["timestamp"] = result["timestamp"].astype("int64")
    result["is_observed"] = _observed(result["is_observed"])
    result = result.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    if len(result) < 2 or np.any(np.diff(result["timestamp"].to_numpy()) <= 0):
        raise ValueError("market timestamps must be strictly increasing")
    if require_features:
        result[list(FEATURE_COLUMNS)] = result[list(FEATURE_COLUMNS)].apply(
            pd.to_numeric,
            errors="coerce",
        )
        if result[list(FEATURE_COLUMNS)].isna().any().any():
            raise ValueError("local market features contain missing values")
    return result.reset_index(drop=True)


def _exact_return(frame: pd.DataFrame, minutes: int) -> np.ndarray:
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64)
    close = pd.Series(frame["close"].to_numpy(dtype=np.float64), index=timestamps)
    observed = pd.Series(frame["is_observed"].to_numpy(dtype=bool), index=timestamps)
    prior_timestamps = timestamps - minutes * 60
    prior_close = close.reindex(prior_timestamps).to_numpy(dtype=np.float64)
    prior_observed = observed.reindex(prior_timestamps).fillna(False).to_numpy(dtype=bool)
    current_observed = frame["is_observed"].to_numpy(dtype=bool)
    result = (frame["close"].to_numpy(dtype=np.float64) / prior_close - 1.0) * 100.0
    result[~(prior_observed & current_observed)] = np.nan
    return result


def _price_age_minutes(frame: pd.DataFrame) -> np.ndarray:
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64)
    observed_timestamps = pd.Series(
        np.where(frame["is_observed"].to_numpy(dtype=bool), timestamps, np.nan),
    ).ffill()
    return (timestamps - observed_timestamps.to_numpy(dtype=np.float64)) / 60.0


def _future_return(
    frame: pd.DataFrame,
    minutes: int,
    *,
    maximum_price_age_minutes: int,
) -> tuple[np.ndarray, np.ndarray]:
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64)
    close = pd.Series(frame["close"].to_numpy(dtype=np.float64), index=timestamps)
    price_age = pd.Series(_price_age_minutes(frame), index=timestamps)
    future_timestamps = timestamps + minutes * 60
    future_close = close.reindex(future_timestamps).to_numpy(dtype=np.float64)
    future_age = price_age.reindex(future_timestamps).to_numpy(dtype=np.float64)
    future_observed = np.isfinite(future_age) & (future_age <= maximum_price_age_minutes)
    returns = (future_close / frame["close"].to_numpy(dtype=np.float64) - 1.0) * 100.0
    returns[~future_observed] = np.nan
    return returns, future_observed


def build_slow_tcn_dataset(
    local_market: pd.DataFrame,
    global_market: pd.DataFrame,
    *,
    cadence_minutes: int = 15,
    horizons_minutes: Iterable[int] = (240, 1_440),
    actionable_move_pct: float = 0.25,
    maximum_price_age_minutes: int = 15,
) -> pd.DataFrame:
    """Build causal 15-minute decision rows with synchronized global evidence."""

    horizons = tuple(int(item) for item in horizons_minutes)
    if cadence_minutes <= 0 or not horizons or any(item <= 0 for item in horizons):
        raise ValueError("cadence and horizons must be positive")
    if (
        any(horizon % cadence_minutes for horizon in horizons)
        or actionable_move_pct <= 0
        or maximum_price_age_minutes < 0
    ):
        raise ValueError("horizons must align with cadence and edge must be positive")

    local = _validated_market_frame(local_market, require_features=True)
    global_frame = _validated_market_frame(global_market, require_features=False)

    for horizon in horizons:
        future_return, future_observed = _future_return(
            local,
            horizon,
            maximum_price_age_minutes=maximum_price_age_minutes,
        )
        local[f"future_return_{horizon}m_pct"] = future_return
        local[f"future_observed_{horizon}m"] = future_observed
        labels = np.full(len(local), "HOLD", dtype=object)
        labels[future_return >= actionable_move_pct] = "BUY"
        labels[future_return <= -actionable_move_pct] = "SELL"
        labels[~np.isfinite(future_return)] = None
        local[f"label_{horizon}m"] = labels

    global_features = global_frame[["timestamp"]].copy()
    for horizon in (15, 60, 240):
        global_features[f"global_return_{horizon}m_pct"] = _exact_return(global_frame, horizon)

    local["_price_age_minutes"] = _price_age_minutes(local)
    cadence_seconds = cadence_minutes * 60
    sampled = local.loc[local["timestamp"] % cadence_seconds == 0].copy()
    sampled = sampled.merge(global_features, on="timestamp", how="inner", validate="one_to_one")
    seconds_of_day = sampled["timestamp"].to_numpy(dtype=np.int64) % 86_400
    day_number = sampled["timestamp"].to_numpy(dtype=np.int64) // 86_400
    sampled["utc_time_sin"] = np.sin(2.0 * np.pi * seconds_of_day / 86_400.0)
    sampled["utc_time_cos"] = np.cos(2.0 * np.pi * seconds_of_day / 86_400.0)
    weekday = (day_number + 3) % 7  # Unix epoch began on a Thursday.
    sampled["utc_weekday_sin"] = np.sin(2.0 * np.pi * weekday / 7.0)
    sampled["utc_weekday_cos"] = np.cos(2.0 * np.pi * weekday / 7.0)
    sampled["cross_market_return_gap_60m_pct"] = (
        sampled["return_60m_pct"] - sampled["global_return_60m_pct"]
    )
    sampled["is_observed"] = sampled["_price_age_minutes"] <= maximum_price_age_minutes

    sampled = sampled.loc[sampled[list(SLOW_TCN_FEATURE_COLUMNS)].notna().all(axis=1)].copy()
    sampled = sampled.sort_values("timestamp").reset_index(drop=True)
    if sampled.empty:
        raise ValueError("slow TCN dataset contains no eligible synchronized rows")
    breaks = sampled["timestamp"].diff().fillna(cadence_seconds) != cadence_seconds
    sampled["segment_id"] = breaks.cumsum().astype("int64") - 1
    export_columns = [
        "timestamp",
        "segment_id",
        "is_observed",
        "close",
        *SLOW_TCN_FEATURE_COLUMNS,
    ]
    for horizon in horizons:
        export_columns.extend(
            [
                f"future_observed_{horizon}m",
                f"future_return_{horizon}m_pct",
                f"label_{horizon}m",
            ]
        )
    return sampled[export_columns]
