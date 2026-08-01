from __future__ import annotations

import os
from pathlib import Path

import torch

from backend.ml.sequences import RobustFeatureScaler, RobustTargetScaler
from backend.ml.tcn import QuantileTCN, TCNConfig


CHECKPOINT_FORMAT_VERSION = 1


def save_tcn_checkpoint(
    path: Path,
    model: QuantileTCN,
    payload: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    document = {
        "checkpoint_format_version": CHECKPOINT_FORMAT_VERSION,
        "model_state": model.state_dict(),
        **payload,
    }
    try:
        torch.save(document, temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_tcn_checkpoint(
    path: Path,
    *,
    device: torch.device | str = "cpu",
) -> tuple[QuantileTCN, RobustFeatureScaler, RobustTargetScaler, dict[str, object]]:
    document = torch.load(path, map_location=device, weights_only=True)
    if not isinstance(document, dict):
        raise ValueError("TCN checkpoint must be a dictionary")
    required = {
        "checkpoint_format_version",
        "model_state",
        "tcn_config",
        "scaler",
        "target_scaler",
        "sequence_length",
    }
    missing = required.difference(document)
    if missing:
        raise ValueError(f"TCN checkpoint is missing: {', '.join(sorted(missing))}")
    if int(document["checkpoint_format_version"]) != CHECKPOINT_FORMAT_VERSION:
        raise ValueError("unsupported TCN checkpoint format")

    raw_config = dict(document["tcn_config"])
    raw_config.pop("receptive_field", None)
    raw_config["horizons_minutes"] = tuple(int(item) for item in raw_config["horizons_minutes"])
    config = TCNConfig(**raw_config)
    model = QuantileTCN(config).to(device)
    model.load_state_dict(document["model_state"], strict=True)
    model.eval()
    feature_scaler = RobustFeatureScaler.from_dict(dict(document["scaler"]))
    target_scaler = RobustTargetScaler.from_dict(dict(document["target_scaler"]))
    if target_scaler.horizons_minutes != config.horizons_minutes:
        raise ValueError("checkpoint target scaler horizons do not match the model")
    metadata = {key: value for key, value in document.items() if key != "model_state"}
    return model, feature_scaler, target_scaler, metadata
