from __future__ import annotations

import numpy as np
import torch

from backend.ml.checkpoints import save_tcn_checkpoint
from backend.ml.dataset import FEATURE_COLUMNS
from backend.ml.inference import TCNAdvisor, qualify_advisory_checkpoint
from backend.ml.sequences import RobustFeatureScaler, RobustTargetScaler
from backend.ml.tcn import QuantileTCN, TCNConfig


def _checkpoint(tmp_path, *, qualified: bool):
    config = TCNConfig(input_channels=len(FEATURE_COLUMNS), channels=8)
    model = QuantileTCN(config)
    features = np.arange(300 * len(FEATURE_COLUMNS), dtype=np.float32).reshape(300, -1)
    feature_scaler = RobustFeatureScaler.fit(features)
    target_scaler = RobustTargetScaler.fit(
        np.array([[-1.0, -2.0], [0.0, 0.0], [1.0, 2.0]], dtype=np.float32),
        (15, 60),
    )
    path = tmp_path / "advisor.pt"
    payload = {
        "tcn_config": config.as_dict(),
        "scaler": feature_scaler.as_dict(),
        "target_scaler": target_scaler.as_dict(),
        "sequence_length": 240,
        "direction_target_mode": "barrier",
        "direction_temperatures": [1.2, 1.3],
        "test_evaluated": qualified,
        "test_direction_metrics": {
            "15m": {"balanced_accuracy": 0.52},
            "60m": {"balanced_accuracy": 0.54},
        },
    }
    save_tcn_checkpoint(path, model, payload)
    return path, features[-240:]


def test_advisor_returns_probabilities_without_execution_capability(tmp_path):
    path, features = _checkpoint(tmp_path, qualified=True)
    advisor = TCNAdvisor(path)

    result = advisor.predict(features)

    assert result["status"] == "RESEARCH_ONLY"
    assert result["execution_eligible"] is False
    assert result["can_authorize_order"] is False
    assert len(result["forecast"]) == 2
    for forecast in result["forecast"]:
        assert abs(sum(forecast["first_touch_probabilities"].values()) - 1.0) < 1e-6


def test_advisor_fails_closed_before_reserved_test(tmp_path):
    path, features = _checkpoint(tmp_path, qualified=False)
    advisor = TCNAdvisor(path)

    result = advisor.predict(features)

    assert result["status"] == "UNAVAILABLE"
    assert result["execution_eligible"] is False
    assert "reserved temporal test" in " ".join(result["failures"])


def test_advisor_rejects_wrong_or_nonfinite_context(tmp_path):
    path, features = _checkpoint(tmp_path, qualified=True)
    advisor = TCNAdvisor(path)

    with np.testing.assert_raises(ValueError):
        advisor.predict(features[:-1])
    features[-1, 0] = np.nan
    with np.testing.assert_raises(ValueError):
        advisor.predict(features)


def test_qualification_rejects_malformed_calibration_metadata():
    result = qualify_advisory_checkpoint(
        {
            "direction_target_mode": "barrier",
            "test_evaluated": True,
            "tcn_config": {"horizons_minutes": [15, 60]},
            "direction_temperatures": [float("nan"), "bad"],
            "test_direction_metrics": {
                "15m": {"balanced_accuracy": "bad"},
                "60m": {"balanced_accuracy": float("nan")},
            },
        }
    )

    assert result.qualified_for_research is False
    assert len(result.failures) == 3
