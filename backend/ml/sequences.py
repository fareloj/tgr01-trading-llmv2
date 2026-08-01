from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

from backend.ml.dataset import FEATURE_COLUMNS


@dataclass(frozen=True)
class ArrayMarketData:
    timestamps: np.ndarray
    segment_ids: np.ndarray
    is_observed: np.ndarray
    features: np.ndarray
    targets: np.ndarray
    horizons_minutes: tuple[int, ...]

    def __post_init__(self) -> None:
        rows = len(self.timestamps)
        if rows == 0:
            raise ValueError("market data cannot be empty")
        if self.segment_ids.shape != (rows,):
            raise ValueError("segment_ids must match timestamps")
        if self.is_observed.shape != (rows,):
            raise ValueError("is_observed must match timestamps")
        if self.features.shape != (rows, len(FEATURE_COLUMNS)):
            raise ValueError("feature matrix has an unexpected shape")
        if self.targets.shape != (rows, len(self.horizons_minutes)):
            raise ValueError("target matrix has an unexpected shape")


@dataclass(frozen=True)
class TemporalRanges:
    train: tuple[int, int]
    validation: tuple[int, int]
    test: tuple[int, int]
    purge_minutes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "train": list(self.train),
            "validation": list(self.validation),
            "test": list(self.test),
            "purge_minutes": self.purge_minutes,
        }


@dataclass(frozen=True)
class RobustFeatureScaler:
    median: np.ndarray
    scale: np.ndarray
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS
    clip_value: float = 10.0

    @classmethod
    def fit(
        cls,
        values: np.ndarray,
        *,
        maximum_fit_rows: int = 1_000_000,
        clip_value: float = 10.0,
    ) -> "RobustFeatureScaler":
        if values.ndim != 2 or values.shape[1] != len(FEATURE_COLUMNS):
            raise ValueError("values must be a two-dimensional feature matrix")
        if len(values) == 0:
            raise ValueError("cannot fit a scaler on an empty matrix")
        if maximum_fit_rows <= 0 or clip_value <= 0:
            raise ValueError("scaler limits must be positive")
        stride = max(1, int(np.ceil(len(values) / maximum_fit_rows)))
        sample = values[::stride].astype(np.float64, copy=False)
        if not np.isfinite(sample).all():
            raise ValueError("scaler input contains non-finite values")
        median = np.median(sample, axis=0)
        q25, q75 = np.quantile(sample, (0.25, 0.75), axis=0)
        scale = q75 - q25
        scale = np.where(scale > 1e-8, scale, 1.0)
        return cls(
            median=median.astype(np.float32),
            scale=scale.astype(np.float32),
            clip_value=float(clip_value),
        )

    def transform(self, values: np.ndarray) -> np.ndarray:
        if values.ndim != 2 or values.shape[1] != len(self.feature_columns):
            raise ValueError("values do not match the scaler feature schema")
        transformed = (values.astype(np.float32, copy=False) - self.median) / self.scale
        np.clip(transformed, -self.clip_value, self.clip_value, out=transformed)
        if not np.isfinite(transformed).all():
            raise ValueError("scaled features contain non-finite values")
        return transformed

    def as_dict(self) -> dict[str, object]:
        return {
            "feature_columns": list(self.feature_columns),
            "median": self.median.tolist(),
            "scale": self.scale.tolist(),
            "clip_value": self.clip_value,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RobustFeatureScaler":
        columns = tuple(str(item) for item in value["feature_columns"])
        if columns != FEATURE_COLUMNS:
            raise ValueError("checkpoint scaler uses a different feature schema")
        return cls(
            median=np.asarray(value["median"], dtype=np.float32),
            scale=np.asarray(value["scale"], dtype=np.float32),
            feature_columns=columns,
            clip_value=float(value["clip_value"]),
        )


@dataclass(frozen=True)
class RobustTargetScaler:
    center: np.ndarray
    scale: np.ndarray
    horizons_minutes: tuple[int, ...]

    @classmethod
    def fit(
        cls,
        targets: np.ndarray,
        horizons_minutes: Iterable[int],
        *,
        maximum_fit_rows: int = 1_000_000,
    ) -> "RobustTargetScaler":
        horizons = tuple(int(item) for item in horizons_minutes)
        if targets.ndim != 2 or targets.shape[1] != len(horizons):
            raise ValueError("targets do not match horizons")
        finite = targets[np.isfinite(targets).all(axis=1)]
        if len(finite) == 0 or maximum_fit_rows <= 0:
            raise ValueError("cannot fit target scaler without finite targets")
        stride = max(1, int(np.ceil(len(finite) / maximum_fit_rows)))
        sample = finite[::stride].astype(np.float64, copy=False)
        center = np.median(sample, axis=0)
        q25, q75 = np.quantile(sample, (0.25, 0.75), axis=0)
        scale = np.where(q75 - q25 > 1e-8, q75 - q25, 1.0)
        return cls(
            center=center.astype(np.float32),
            scale=scale.astype(np.float32),
            horizons_minutes=horizons,
        )

    def transform_tensor(self, targets: torch.Tensor) -> torch.Tensor:
        center = targets.new_tensor(self.center)
        scale = targets.new_tensor(self.scale)
        return (targets - center) / scale

    def inverse_tensor(self, predictions: torch.Tensor) -> torch.Tensor:
        center = predictions.new_tensor(self.center).view(1, -1, 1)
        scale = predictions.new_tensor(self.scale).view(1, -1, 1)
        return predictions * scale + center

    def as_dict(self) -> dict[str, object]:
        return {
            "center": self.center.tolist(),
            "scale": self.scale.tolist(),
            "horizons_minutes": list(self.horizons_minutes),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RobustTargetScaler":
        return cls(
            center=np.asarray(value["center"], dtype=np.float32),
            scale=np.asarray(value["scale"], dtype=np.float32),
            horizons_minutes=tuple(int(item) for item in value["horizons_minutes"]),
        )


def load_market_arrays(path: Path, horizons_minutes: Iterable[int]) -> ArrayMarketData:
    horizons = tuple(int(item) for item in horizons_minutes)
    if not horizons or any(item <= 0 for item in horizons):
        raise ValueError("horizons must contain positive minute values")
    target_columns = [f"future_return_{item}m_pct" for item in horizons]
    use_columns = ["timestamp", "segment_id", "is_observed", *FEATURE_COLUMNS, *target_columns]
    dtypes = {
        "timestamp": "int64",
        "segment_id": "int64",
        "is_observed": "boolean",
        **{column: "float32" for column in FEATURE_COLUMNS},
        **{column: "float32" for column in target_columns},
    }
    frame = pd.read_csv(path, usecols=use_columns, dtype=dtypes)
    if frame[["timestamp", "segment_id", "is_observed", *FEATURE_COLUMNS]].isna().any().any():
        raise ValueError(f"dataset contains missing timestamps, segments, or features: {path}")
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64, copy=True)
    if np.any(np.diff(timestamps) <= 0):
        raise ValueError("dataset timestamps must be strictly increasing")
    segment_ids = derive_continuous_segments(timestamps)
    return ArrayMarketData(
        timestamps=timestamps,
        # Exported segment ids are local to each source chunk. Rebuilding them
        # from the merged timeline prevents false boundaries between files.
        segment_ids=segment_ids,
        is_observed=frame["is_observed"].to_numpy(dtype=bool, copy=True),
        features=frame[list(FEATURE_COLUMNS)].to_numpy(dtype=np.float32, copy=True),
        # Missing targets are expected near the end of a continuous segment.
        # They remain in the timeline and are filtered only as sequence ends.
        targets=frame[target_columns].to_numpy(dtype=np.float32, copy=True),
        horizons_minutes=horizons,
    )


def derive_continuous_segments(
    timestamps: np.ndarray,
    *,
    timeframe_seconds: int = 60,
) -> np.ndarray:
    if timestamps.ndim != 1 or len(timestamps) == 0:
        raise ValueError("timestamps must be a non-empty vector")
    if timeframe_seconds <= 0 or np.any(np.diff(timestamps) <= 0):
        raise ValueError("timestamps or timeframe are invalid")
    starts = np.ones(len(timestamps), dtype=bool)
    starts[1:] = np.diff(timestamps) != timeframe_seconds
    return np.cumsum(starts, dtype=np.int64) - 1


def chronological_ranges(
    timestamps: np.ndarray,
    *,
    train_ratio: float = 0.60,
    validation_ratio: float = 0.20,
    purge_minutes: int = 60,
) -> TemporalRanges:
    if timestamps.ndim != 1 or len(timestamps) < 15:
        raise ValueError("timestamps are too small for temporal ranges")
    if not 0 < train_ratio < 1 or not 0 < validation_ratio < 1:
        raise ValueError("split ratios must be between zero and one")
    if train_ratio + validation_ratio >= 1 or purge_minutes < 0:
        raise ValueError("invalid split ratios or purge")
    if np.any(np.diff(timestamps) <= 0):
        raise ValueError("timestamps must be strictly increasing")

    train_cut = int(len(timestamps) * train_ratio)
    validation_cut = int(len(timestamps) * (train_ratio + validation_ratio))
    validation_start = int(timestamps[train_cut])
    test_start = int(timestamps[validation_cut])
    purge_seconds = purge_minutes * 60
    train_end = int(np.searchsorted(timestamps, validation_start - purge_seconds, side="left"))
    validation_end = int(np.searchsorted(timestamps, test_start - purge_seconds, side="left"))
    ranges = TemporalRanges(
        train=(0, train_end),
        validation=(train_cut, validation_end),
        test=(validation_cut, len(timestamps)),
        purge_minutes=purge_minutes,
    )
    if any(end <= start for start, end in (ranges.train, ranges.validation, ranges.test)):
        raise ValueError("purge interval produced an empty temporal range")
    return ranges


def split_temporal_bounds(
    timestamps: np.ndarray,
    bounds: tuple[int, int],
    *,
    first_ratio: float = 0.60,
    purge_minutes: int = 60,
) -> tuple[tuple[int, int], tuple[int, int]]:
    start, end = bounds
    if not 0 <= start < end <= len(timestamps):
        raise ValueError("temporal bounds are invalid")
    if not 0 < first_ratio < 1 or purge_minutes < 0:
        raise ValueError("split ratio or purge is invalid")
    cut = start + int((end - start) * first_ratio)
    second_start_timestamp = int(timestamps[cut])
    first_end = int(
        np.searchsorted(
            timestamps,
            second_start_timestamp - purge_minutes * 60,
            side="left",
        )
    )
    first = (start, min(first_end, end))
    second = (cut, end)
    if first[1] <= first[0] or second[1] <= second[0]:
        raise ValueError("purge interval produced an empty temporal subrange")
    return first, second


def continuous_end_indices(
    timestamps: np.ndarray,
    segment_ids: np.ndarray,
    *,
    start: int,
    end: int,
    sequence_length: int,
    stride: int,
    timeframe_seconds: int = 60,
) -> np.ndarray:
    if sequence_length <= 0 or stride <= 0 or timeframe_seconds <= 0:
        raise ValueError("sequence settings must be positive")
    if not 0 <= start < end <= len(timestamps) or len(segment_ids) != len(timestamps):
        raise ValueError("invalid sequence range")
    local_timestamps = timestamps[start:end]
    local_segments = segment_ids[start:end]
    breaks = np.ones(len(local_timestamps), dtype=bool)
    if len(local_timestamps) > 1:
        breaks[1:] = (np.diff(local_timestamps) != timeframe_seconds) | (
            local_segments[1:] != local_segments[:-1]
        )
    positions = np.arange(len(local_timestamps), dtype=np.int64)
    run_starts = np.maximum.accumulate(np.where(breaks, positions, 0))
    run_lengths = positions - run_starts + 1
    valid = positions[run_lengths >= sequence_length]
    return (valid[::stride] + start).astype(np.int64, copy=False)


def observed_target_indices(data: ArrayMarketData, indices: np.ndarray) -> np.ndarray:
    if indices.ndim != 1:
        raise ValueError("indices must be a vector")
    if len(indices) and (int(indices.min()) < 0 or int(indices.max()) >= len(data.timestamps)):
        raise ValueError("indices are outside the market data")
    eligible = data.is_observed[indices] & np.isfinite(data.targets[indices]).all(axis=1)
    return indices[eligible]


class MarketSequenceDataset(Dataset):
    def __init__(
        self,
        data: ArrayMarketData,
        indices: np.ndarray,
        *,
        sequence_length: int,
    ) -> None:
        if indices.ndim != 1 or len(indices) == 0:
            raise ValueError("sequence dataset requires at least one end index")
        if sequence_length <= 0 or int(indices.min()) < sequence_length - 1:
            raise ValueError("invalid sequence indices or length")
        self.data = data
        self.indices = indices.astype(np.int64, copy=False)
        self.sequence_length = sequence_length

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, item: int) -> tuple[torch.Tensor, torch.Tensor]:
        end = int(self.indices[item])
        start = end - self.sequence_length + 1
        features = np.ascontiguousarray(self.data.features[start : end + 1].T)
        targets = np.ascontiguousarray(self.data.targets[end])
        return torch.from_numpy(features), torch.from_numpy(targets)


@dataclass(frozen=True)
class DeviceMarketData:
    features: torch.Tensor
    targets: torch.Tensor

    @classmethod
    def from_arrays(cls, data: ArrayMarketData, device: torch.device) -> "DeviceMarketData":
        if device.type != "cuda":
            raise ValueError("device-resident batching is intended for CUDA")
        return cls(
            features=torch.from_numpy(data.features).to(device),
            targets=torch.from_numpy(data.targets).to(device),
        )


class DeviceSequenceBatcher:
    """Vectorize complete sequence batches directly on the CUDA device."""

    def __init__(
        self,
        data: DeviceMarketData,
        indices: np.ndarray,
        *,
        sequence_length: int,
        batch_size: int,
        shuffle: bool,
    ) -> None:
        if indices.ndim != 1 or len(indices) == 0:
            raise ValueError("device batcher requires at least one end index")
        if sequence_length <= 0 or batch_size <= 0:
            raise ValueError("device batch settings must be positive")
        self.data = data
        self.indices = torch.from_numpy(indices.astype(np.int64, copy=False)).to(data.features.device)
        self.offsets = torch.arange(
            sequence_length - 1,
            -1,
            -1,
            dtype=torch.int64,
            device=data.features.device,
        )
        self.batch_size = batch_size
        self.shuffle = shuffle

    def __len__(self) -> int:
        return int(np.ceil(len(self.indices) / self.batch_size))

    def __iter__(self):
        if self.shuffle:
            order = torch.randperm(len(self.indices), device=self.indices.device)
            indices = self.indices[order]
        else:
            indices = self.indices
        for start in range(0, len(indices), self.batch_size):
            ends = indices[start : start + self.batch_size]
            rows = ends[:, None] - self.offsets[None, :]
            features = self.data.features[rows].permute(0, 2, 1).contiguous()
            yield features, self.data.targets[ends]


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
