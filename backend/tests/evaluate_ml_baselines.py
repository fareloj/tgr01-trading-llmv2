from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.ml.baselines import evaluate_baselines
from backend.ml.dataset import chronological_split, select_labeled_horizon
from backend.ml.readiness import assess_training_readiness


REPORTS_DIR = PROJECT_DIR / "backend" / "reports"


def _label_distribution(frame: pd.DataFrame, horizon: int) -> dict[str, int]:
    return {
        str(label): int(count)
        for label, count in frame[f"label_{horizon}m"].value_counts().to_dict().items()
    }


def _markdown(report: dict) -> str:
    lines = [
        "# Deterministic ML Baseline Report",
        "",
        "This report is a chronological research baseline, not evidence of live profitability.",
        "",
        "## Split",
        "",
        f"- Purge: {report['purge_minutes']} minutes",
        f"- Ready for learned-model experiments: {report['training_readiness']['ready_for_model_experiments']}",
    ]
    for split_name, split_data in report["splits"].items():
        lines.append(f"- {split_name}: {split_data['rows']} rows; labels={split_data['labels']}")

    for split_name, split_data in report["splits"].items():
        lines.extend(["", f"## {split_name.title()}", "", "| Baseline | Accuracy | Macro F1 | Trades | Win rate | Return | Max DD |", "|---|---:|---:|---:|---:|---:|---:|"])
        for name, metrics in split_data["baselines"].items():
            lines.append(
                f"| {name} | {metrics['accuracy']:.4f} | {metrics['macro_f1']:.4f} | "
                f"{metrics['trade_count']} | {metrics['win_rate_after_cost']:.4f} | "
                f"{metrics['compounded_strategy_return_pct']:+.4f}% | "
                f"{metrics['maximum_drawdown_pct']:.4f}% |"
            )
    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "A candidate model must be selected on validation data and reported once on the untouched test split. "
            "Costs are estimates; this report does not model order-book depth, latency, spread variation, or taxes.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic baselines on a purged temporal split.")
    parser.add_argument("--input", default=str(REPORTS_DIR / "last_ml_dataset.csv"))
    parser.add_argument("--horizon", type=int, default=15)
    parser.add_argument("--round-trip-cost-pct", type=float, default=0.15)
    parser.add_argument("--train-ratio", type=float, default=0.60)
    parser.add_argument("--validation-ratio", type=float, default=0.20)
    parser.add_argument("--purge-minutes", type=int, default=60)
    parser.add_argument("--json-out", default=str(REPORTS_DIR / "last_ml_baselines.json"))
    parser.add_argument("--md-out", default=str(REPORTS_DIR / "last_ml_baselines.md"))
    args = parser.parse_args()

    if args.purge_minutes < args.horizon:
        raise SystemExit("purge-minutes must be greater than or equal to the evaluated horizon")

    dataset = pd.read_csv(args.input)
    required = {"timestamp", f"label_{args.horizon}m", f"future_return_{args.horizon}m_pct"}
    missing = required.difference(dataset.columns)
    if missing:
        raise SystemExit(f"Dataset missing required columns: {', '.join(sorted(missing))}")
    original_rows = len(dataset)
    dataset = select_labeled_horizon(dataset, args.horizon)
    if dataset.empty:
        raise SystemExit("No rows have an observed future candle for the requested horizon")
    split = chronological_split(
        dataset,
        train_ratio=args.train_ratio,
        validation_ratio=args.validation_ratio,
        purge_minutes=args.purge_minutes,
    )

    partitions = {"train": split.train, "validation": split.validation, "test": split.test}
    report = {
        "dataset_path": str(Path(args.input).resolve()),
        "horizon_minutes": args.horizon,
        "round_trip_cost_pct": args.round_trip_cost_pct,
        "source_rows": original_rows,
        "eligible_horizon_rows": len(dataset),
        "purge_minutes": split.purge_minutes,
        "training_readiness": assess_training_readiness(dataset, horizon_minutes=args.horizon),
        "splits": {},
    }
    for name, frame in partitions.items():
        report["splits"][name] = {
            "rows": len(frame),
            "first_timestamp": int(frame["timestamp"].min()),
            "last_timestamp": int(frame["timestamp"].max()),
            "labels": _label_distribution(frame, args.horizon),
            "baselines": evaluate_baselines(
                frame,
                horizon_minutes=args.horizon,
                round_trip_cost_pct=args.round_trip_cost_pct,
            ),
        }

    json_output = Path(args.json_out)
    markdown_output = Path(args.md_out)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_output.write_text(_markdown(report), encoding="utf-8")

    print(_markdown(report))
    print(f"JSON: {json_output.resolve()}")
    print(f"Markdown: {markdown_output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
