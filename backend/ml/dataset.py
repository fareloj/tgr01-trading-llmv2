from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import pandas as pd


REQUIRED_CANDLE_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
FEATURE_COLUMNS = (
    "return_1m_pct",
    "return_5m_pct",
    "return_15m_pct",
    "return_60m_pct",
    "volatility_15m_pct",
    "volatility_60m_pct",
    "rsi_14",
    "macd_hist_pct",
    "atr_14_pct",
    "ema_9_21_spread_pct",
    "bollinger_z_20",
    "volume_z_20",
    "donchian_position_20",
    "drawdown_60_pct",
    "drawdown_240_pct",
    "observed_coverage_60",
    "observed_coverage_240",
)


@dataclass(frozen=True)
class DatasetConfig:
    timeframe_seconds: int = 60
    horizons_minutes: tuple[int, ...] = (15, 60)
    primary_horizon_minutes: int = 15
    round_trip_cost_pct: float = 0.15
    minimum_net_edge_pct: float = 0.05
    minimum_history_candles: int = 240
    max_fill_gap_minutes: int = 15
    minimum_observed_coverage: float = 0.80
    require_observed_future: bool = True

    def __post_init__(self) -> None:
        if self.timeframe_seconds <= 0:
            raise ValueError("timeframe_seconds must be positive")
        if not self.horizons_minutes or any(item <= 0 for item in self.horizons_minutes):
            raise ValueError("horizons_minutes must contain positive values")
        if self.primary_horizon_minutes not in self.horizons_minutes:
            raise ValueError("primary_horizon_minutes must be included in horizons_minutes")
        if self.round_trip_cost_pct < 0 or self.minimum_net_edge_pct < 0:
            raise ValueError("cost and minimum edge must be non-negative")
        if self.minimum_history_candles < 240:
            raise ValueError("minimum_history_candles must be at least 240")
        if self.max_fill_gap_minutes < 1:
            raise ValueError("max_fill_gap_minutes must be positive")
        if not 0.0 < self.minimum_observed_coverage <= 1.0:
            raise ValueError("minimum_observed_coverage must be in (0, 1]")

    @property
    def actionable_move_pct(self) -> float:
        return self.round_trip_cost_pct + self.minimum_net_edge_pct

    def as_dict(self) -> dict:
        result = asdict(self)
        result["horizons_minutes"] = list(self.horizons_minutes)
        result["actionable_move_pct"] = self.actionable_move_pct
        return result


@dataclass(frozen=True)
class TemporalSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    purge_minutes: int

    def sizes(self) -> dict[str, int]:
        return {
            "train": len(self.train),
            "validation": len(self.validation),
            "test": len(self.test),
        }


def _validate_candles(candles: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_CANDLE_COLUMNS if column not in candles.columns]
    if missing:
        raise ValueError(f"missing candle columns: {', '.join(missing)}")

    frame = candles.loc[:, list(REQUIRED_CANDLE_COLUMNS)].copy()
    for column in REQUIRED_CANDLE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.isna().any().any():
        raise ValueError("candles contain null or non-numeric values")

    frame["timestamp"] = frame["timestamp"].astype("int64")
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    if frame.empty:
        raise ValueError("no candles supplied")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (frame["volume"] < 0).any():
        raise ValueError("volume must be non-negative")

    upper = frame[["open", "close", "low"]].max(axis=1)
    lower = frame[["open", "close", "high"]].min(axis=1)
    if (frame["high"] < upper).any() or (frame["low"] > lower).any():
        raise ValueError("candles contain inconsistent OHLC ranges")
    return frame


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    losses = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs = gains / losses.replace(0.0, float("nan"))
    result = 100.0 - (100.0 / (1.0 + rs))
    result = result.mask((losses == 0) & (gains > 0), 100.0)
    result = result.mask((gains == 0) & (losses > 0), 0.0)
    return result.mask((gains == 0) & (losses == 0), 50.0)


def _regularize_candles(frame: pd.DataFrame, config: DatasetConfig) -> pd.DataFrame:
    gap_limit_seconds = config.max_fill_gap_minutes * 60
    starts_new_segment = frame["timestamp"].diff().fillna(0) > gap_limit_seconds
    frame = frame.copy()
    frame["segment_id"] = starts_new_segment.cumsum().astype("int64")
    segments = []
    for segment_id, segment in frame.groupby("segment_id", sort=False):
        indexed = segment.set_index("timestamp").sort_index()
        offset = int(indexed.index.min())
        if any((int(timestamp) - offset) % config.timeframe_seconds for timestamp in indexed.index):
            raise ValueError("candle timestamps are not aligned to the configured timeframe")
        regular_index = range(
            int(indexed.index.min()),
            int(indexed.index.max()) + config.timeframe_seconds,
            config.timeframe_seconds,
        )
        regular = indexed.reindex(regular_index)
        regular["is_observed"] = regular["close"].notna()
        regular["close"] = regular["close"].ffill()
        for column in ("open", "high", "low"):
            regular[column] = regular[column].where(regular["is_observed"], regular["close"])
        regular["volume"] = regular["volume"].where(regular["is_observed"], 0.0)
        regular["segment_id"] = int(segment_id)
        regular.index.name = "timestamp"
        segments.append(regular.reset_index())
    return pd.concat(segments, ignore_index=True)


def _segment_features(segment: pd.DataFrame) -> pd.DataFrame:
    result = segment.copy()
    close = result["close"]
    previous_close = close.shift(1)

    result["return_1m_pct"] = close.pct_change(1, fill_method=None) * 100.0
    for minutes in (5, 15, 60):
        result[f"return_{minutes}m_pct"] = close.pct_change(minutes, fill_method=None) * 100.0

    one_minute_return = close.pct_change(fill_method=None) * 100.0
    result["volatility_15m_pct"] = one_minute_return.rolling(15, min_periods=15).std(ddof=0)
    result["volatility_60m_pct"] = one_minute_return.rolling(60, min_periods=60).std(ddof=0)
    result["rsi_14"] = _rsi(close)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    result["macd_hist_pct"] = ((macd - macd_signal) / close) * 100.0

    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr_14_pct"] = (true_range.rolling(14, min_periods=14).mean() / close) * 100.0

    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    result["ema_9_21_spread_pct"] = ((ema9 - ema21) / close) * 100.0

    middle = close.rolling(20, min_periods=20).mean()
    std = close.rolling(20, min_periods=20).std(ddof=0)
    result["bollinger_z_20"] = ((close - middle) / std.replace(0.0, float("nan"))).where(std != 0.0, 0.0)

    volume_mean = result["volume"].rolling(20, min_periods=20).mean()
    volume_std = result["volume"].rolling(20, min_periods=20).std(ddof=0)
    result["volume_z_20"] = (
        (result["volume"] - volume_mean) / volume_std.replace(0.0, float("nan"))
    ).where(volume_std != 0.0, 0.0)

    prior_high = result["high"].shift(1).rolling(20, min_periods=20).max()
    prior_low = result["low"].shift(1).rolling(20, min_periods=20).min()
    channel_width = prior_high - prior_low
    result["donchian_position_20"] = (
        (close - prior_low) / channel_width.replace(0.0, float("nan"))
    ).where(channel_width != 0.0, 0.5)

    result["drawdown_60_pct"] = ((close / close.rolling(60, min_periods=60).max()) - 1.0) * 100.0
    result["drawdown_240_pct"] = ((close / close.rolling(240, min_periods=240).max()) - 1.0) * 100.0
    observed = result["is_observed"].astype(float)
    result["observed_coverage_60"] = observed.rolling(60, min_periods=60).mean()
    result["observed_coverage_240"] = observed.rolling(240, min_periods=240).mean()
    return result


def _assign_future_labels(frame: pd.DataFrame, config: DatasetConfig) -> pd.DataFrame:
    result = frame.copy()
    close_by_timestamp = pd.Series(result["close"].to_numpy(), index=result["timestamp"].to_numpy())
    observed_by_timestamp = pd.Series(result["is_observed"].to_numpy(), index=result["timestamp"].to_numpy())
    for horizon in config.horizons_minutes:
        targets = result["timestamp"] + (horizon * 60)
        future_close = close_by_timestamp.reindex(targets.to_numpy()).to_numpy()
        future_observed = observed_by_timestamp.reindex(targets.to_numpy()).fillna(False).to_numpy(dtype=bool)
        result[f"future_close_{horizon}m"] = future_close
        result[f"future_observed_{horizon}m"] = future_observed
        result[f"future_return_{horizon}m_pct"] = ((future_close / result["close"]) - 1.0) * 100.0

        move = result[f"future_return_{horizon}m_pct"]
        label = pd.Series("HOLD", index=result.index, dtype="object")
        label = label.mask(move >= config.actionable_move_pct, "BUY")
        label = label.mask(move <= -config.actionable_move_pct, "SELL")
        label = label.mask(move.isna(), pd.NA)
        if config.require_observed_future:
            label = label.mask(~future_observed, pd.NA)
        result[f"label_{horizon}m"] = label

    result["label"] = result[f"label_{config.primary_horizon_minutes}m"]
    return result


def _build_featured_market_table(candles: pd.DataFrame, config: DatasetConfig) -> pd.DataFrame:
    frame = _validate_candles(candles)
    frame = _regularize_candles(frame, config)
    featured = pd.concat(
        [_segment_features(segment) for _, segment in frame.groupby("segment_id", sort=False)],
        ignore_index=True,
    )
    featured = _assign_future_labels(featured, config)
    featured[list(FEATURE_COLUMNS)] = featured[list(FEATURE_COLUMNS)].replace(
        [float("inf"), float("-inf")], pd.NA
    )

    segment_position = featured.groupby("segment_id").cumcount() + 1
    featured["history_candles"] = segment_position
    return featured


def build_market_dataset(candles: pd.DataFrame, config: DatasetConfig | None = None) -> pd.DataFrame:
    """Build a causal decision-row table from closed candles.

    Features at timestamp ``t`` use only candles at or before ``t``. Future
    labels require an exact candle at ``t + horizon``; gaps are never bridged.
    Both the decision candle and future candle must be observed.
    """

    config = config or DatasetConfig()
    featured = _build_featured_market_table(candles, config)
    required = list(FEATURE_COLUMNS) + ["label"]
    result = featured.loc[
        (featured["history_candles"] >= config.minimum_history_candles)
        & featured["is_observed"]
        & (featured["observed_coverage_240"] >= config.minimum_observed_coverage)
        & featured[required].notna().all(axis=1)
    ].copy()
    return result.sort_values("timestamp").reset_index(drop=True)


def build_market_sequence_dataset(
    candles: pd.DataFrame,
    config: DatasetConfig | None = None,
) -> pd.DataFrame:
    """Build minute-regular context rows for sequence models.

    Synthetic short-gap rows remain explicitly marked context. Consumers must
    still require observed decision rows and observed future targets.
    """

    config = config or DatasetConfig()
    featured = _build_featured_market_table(candles, config)
    result = featured.loc[
        (featured["history_candles"] >= config.minimum_history_candles)
        & (featured["observed_coverage_240"] >= config.minimum_observed_coverage)
        & featured[list(FEATURE_COLUMNS)].notna().all(axis=1)
    ].copy()
    return result.sort_values("timestamp").reset_index(drop=True)


def select_labeled_horizon(dataset: pd.DataFrame, horizon_minutes: int) -> pd.DataFrame:
    label_column = f"label_{horizon_minutes}m"
    return_column = f"future_return_{horizon_minutes}m_pct"
    required = {"timestamp", label_column, return_column}
    missing = required.difference(dataset.columns)
    if missing:
        raise ValueError(f"dataset missing horizon columns: {', '.join(sorted(missing))}")
    mask = dataset[[label_column, return_column]].notna().all(axis=1)
    observed_column = f"future_observed_{horizon_minutes}m"
    if observed_column in dataset.columns:
        observed = dataset[observed_column]
        if observed.dtype == object:
            observed = observed.astype(str).str.lower().map({"true": True, "false": False})
        mask &= observed.fillna(False).astype(bool)
    return dataset.loc[mask].sort_values("timestamp").reset_index(drop=True)


def chronological_split(
    dataset: pd.DataFrame,
    *,
    train_ratio: float = 0.60,
    validation_ratio: float = 0.20,
    purge_minutes: int = 60,
) -> TemporalSplit:
    """Split chronologically and purge labels that cross split boundaries."""

    if not 0 < train_ratio < 1 or not 0 < validation_ratio < 1:
        raise ValueError("split ratios must be between zero and one")
    if train_ratio + validation_ratio >= 1:
        raise ValueError("train_ratio + validation_ratio must be less than one")
    if purge_minutes < 0:
        raise ValueError("purge_minutes must be non-negative")
    if "timestamp" not in dataset.columns:
        raise ValueError("dataset must contain timestamp")

    ordered = dataset.sort_values("timestamp").reset_index(drop=True)
    if len(ordered) < 15:
        raise ValueError("dataset is too small for train/validation/test splits")
    train_cut = int(len(ordered) * train_ratio)
    validation_cut = int(len(ordered) * (train_ratio + validation_ratio))
    if train_cut <= 0 or validation_cut <= train_cut or validation_cut >= len(ordered):
        raise ValueError("split ratios produce an empty partition")

    validation_start = int(ordered.iloc[train_cut]["timestamp"])
    test_start = int(ordered.iloc[validation_cut]["timestamp"])
    purge_seconds = purge_minutes * 60

    train = ordered.iloc[:train_cut].copy()
    validation = ordered.iloc[train_cut:validation_cut].copy()
    test = ordered.iloc[validation_cut:].copy()
    train = train.loc[train["timestamp"] + purge_seconds < validation_start].reset_index(drop=True)
    validation = validation.loc[validation["timestamp"] + purge_seconds < test_start].reset_index(drop=True)
    test = test.reset_index(drop=True)
    if train.empty or validation.empty or test.empty:
        raise ValueError("purge interval produced an empty partition")
    return TemporalSplit(train=train, validation=validation, test=test, purge_minutes=purge_minutes)


def dataset_metadata(dataset: pd.DataFrame, config: DatasetConfig) -> dict:
    labels = dataset["label"].value_counts(dropna=False).to_dict() if not dataset.empty else {}
    return {
        "config": config.as_dict(),
        "rows": len(dataset),
        "first_timestamp": int(dataset["timestamp"].min()) if not dataset.empty else None,
        "last_timestamp": int(dataset["timestamp"].max()) if not dataset.empty else None,
        "feature_columns": list(FEATURE_COLUMNS),
        "label_distribution": {str(key): int(value) for key, value in labels.items()},
    }


def select_columns_for_export(dataset: pd.DataFrame, horizons: Iterable[int]) -> pd.DataFrame:
    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "segment_id",
        "is_observed",
        "history_candles",
        *FEATURE_COLUMNS,
    ]
    for horizon in horizons:
        columns.extend(
            [
                f"future_close_{horizon}m",
                f"future_observed_{horizon}m",
                f"future_return_{horizon}m_pct",
                f"label_{horizon}m",
            ]
        )
    columns.append("label")
    return dataset.loc[:, columns].copy()
