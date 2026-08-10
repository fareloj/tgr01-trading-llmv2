from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

from backend.ml.checkpoints import load_tcn_checkpoint
from backend.ml.tcn import ordered_quantiles
from backend.ml.training import apply_direction_temperatures


DIRECTION_NAMES = ("SELL", "HOLD", "BUY")


@dataclass(frozen=True)
class AdvisoryQualification:
    qualified_for_research: bool
    failures: tuple[str, ...]


def qualify_advisory_checkpoint(metadata: dict[str, object]) -> AdvisoryQualification:
    failures = []
    if metadata.get("direction_target_mode") != "barrier":
        failures.append("checkpoint was not trained on first-touch barriers")
    if metadata.get("test_evaluated") is not True:
        failures.append("reserved temporal test was not evaluated")
    temperatures = metadata.get("direction_temperatures")
    config = metadata.get("tcn_config")
    horizons = config.get("horizons_minutes", []) if isinstance(config, dict) else []
    valid_temperatures = False
    if isinstance(temperatures, list) and len(temperatures) == len(horizons) and temperatures:
        try:
            numeric_temperatures = np.asarray(temperatures, dtype=np.float64)
            valid_temperatures = bool(
                np.isfinite(numeric_temperatures).all() and (numeric_temperatures > 0).all()
            )
        except (TypeError, ValueError):
            valid_temperatures = False
    if not valid_temperatures:
        failures.append("direction probabilities were not temperature calibrated")
    test_metrics = metadata.get("test_direction_metrics")
    if not isinstance(test_metrics, dict):
        failures.append("test direction metrics are missing")
    else:
        for horizon in ("15m", "60m"):
            values = test_metrics.get(horizon)
            try:
                balanced_accuracy = (
                    float(values.get("balanced_accuracy", 0.0))
                    if isinstance(values, dict)
                    else 0.0
                )
            except (TypeError, ValueError):
                balanced_accuracy = 0.0
            if not np.isfinite(balanced_accuracy) or balanced_accuracy < 0.50:
                failures.append(f"{horizon} balanced accuracy is below the research floor")
    return AdvisoryQualification(not failures, tuple(failures))


class TCNAdvisor:
    """Read-only neural evidence provider with no execution capability."""

    def __init__(self, checkpoint: Path, *, device: str | torch.device = "cpu") -> None:
        self.device = torch.device(device)
        self.model, self.feature_scaler, self.target_scaler, self.metadata = load_tcn_checkpoint(
            checkpoint,
            device=self.device,
        )
        self.sequence_length = int(self.metadata["sequence_length"])
        self.qualification = qualify_advisory_checkpoint(self.metadata)

    def predict(self, feature_context: np.ndarray) -> dict[str, object]:
        if not self.qualification.qualified_for_research:
            return {
                "status": "UNAVAILABLE",
                "failures": list(self.qualification.failures),
                "execution_eligible": False,
                "can_authorize_order": False,
            }
        expected = (self.sequence_length, len(self.feature_scaler.feature_columns))
        if feature_context.shape != expected:
            raise ValueError(f"feature context must have shape {expected}")
        scaled = self.feature_scaler.transform(feature_context)
        tensor = torch.from_numpy(np.ascontiguousarray(scaled.T[None, ...])).to(self.device)
        with torch.inference_mode():
            quantiles, logits = self.model.forward_heads(tensor)
            quantiles = ordered_quantiles(self.target_scaler.inverse_tensor(quantiles))
        raw_logits = logits.cpu().numpy()
        temperatures = np.asarray(self.metadata["direction_temperatures"], dtype=np.float32)
        calibrated = apply_direction_temperatures(raw_logits, temperatures)
        shifted = calibrated - calibrated.max(axis=-1, keepdims=True)
        probabilities = np.exp(shifted)
        probabilities /= probabilities.sum(axis=-1, keepdims=True)
        quantile_values = quantiles.cpu().numpy()[0]
        probability_values = probabilities[0]
        horizons = self.model.config.horizons_minutes
        forecasts = []
        for index, horizon in enumerate(horizons):
            direction_index = int(np.argmax(probability_values[index]))
            forecasts.append(
                {
                    "horizon_minutes": int(horizon),
                    "return_quantiles_pct": {
                        "p10": float(quantile_values[index, 0]),
                        "p50": float(quantile_values[index, 1]),
                        "p90": float(quantile_values[index, 2]),
                    },
                    "first_touch_probabilities": {
                        name: float(probability_values[index, class_index])
                        for class_index, name in enumerate(DIRECTION_NAMES)
                    },
                    "most_likely_first_touch": DIRECTION_NAMES[direction_index],
                    "confidence": float(probability_values[index, direction_index]),
                }
            )
        return {
            "status": "RESEARCH_ONLY",
            "forecast": forecasts,
            "execution_eligible": False,
            "can_authorize_order": False,
            "boundary": (
                "Weak calibrated neural evidence only. Deterministic risk rules and the LLM must "
                "not treat this output as an order or override."
            ),
        }
