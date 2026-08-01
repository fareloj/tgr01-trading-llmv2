from __future__ import annotations

import pandas as pd


def assess_training_readiness(
    dataset: pd.DataFrame,
    *,
    horizon_minutes: int,
    minimum_rows: int = 30_000,
    minimum_calendar_days: int = 30,
    minimum_rows_per_label: int = 1_000,
    minimum_mean_coverage: float = 0.80,
) -> dict:
    """Apply conservative engineering gates before fitting a learned model.

    The defaults are project safeguards, not a statistical theorem. Passing
    them only permits experimentation; it does not imply profitability.
    """

    required = {
        "timestamp",
        f"label_{horizon_minutes}m",
        "observed_coverage_240",
        "segment_id",
    }
    missing = required.difference(dataset.columns)
    if missing:
        raise ValueError(f"dataset missing readiness columns: {', '.join(sorted(missing))}")
    if minimum_rows <= 0 or minimum_calendar_days <= 0 or minimum_rows_per_label <= 0:
        raise ValueError("readiness minimums must be positive")
    if not 0.0 < minimum_mean_coverage <= 1.0:
        raise ValueError("minimum_mean_coverage must be in (0, 1]")

    label_column = f"label_{horizon_minutes}m"
    label_counts = {
        label: int((dataset[label_column] == label).sum())
        for label in ("BUY", "HOLD", "SELL")
    }
    timestamps = pd.to_datetime(dataset["timestamp"], unit="s", utc=True)
    calendar_days = int(timestamps.dt.date.nunique())
    mean_coverage = float(dataset["observed_coverage_240"].mean()) if not dataset.empty else 0.0
    segment_count = int(dataset["segment_id"].nunique())

    checks = {
        "minimum_rows": {
            "passed": len(dataset) >= minimum_rows,
            "actual": len(dataset),
            "required": minimum_rows,
        },
        "minimum_calendar_days": {
            "passed": calendar_days >= minimum_calendar_days,
            "actual": calendar_days,
            "required": minimum_calendar_days,
        },
        "minimum_rows_per_label": {
            "passed": min(label_counts.values(), default=0) >= minimum_rows_per_label,
            "actual": label_counts,
            "required_per_label": minimum_rows_per_label,
        },
        "minimum_mean_coverage": {
            "passed": mean_coverage >= minimum_mean_coverage,
            "actual": round(mean_coverage, 6),
            "required": minimum_mean_coverage,
        },
    }
    return {
        "ready_for_model_experiments": all(item["passed"] for item in checks.values()),
        "checks": checks,
        "segment_count": segment_count,
        "boundary": (
            "Passing these gates permits offline experiments only. It does not authorize paper or live execution."
        ),
    }
