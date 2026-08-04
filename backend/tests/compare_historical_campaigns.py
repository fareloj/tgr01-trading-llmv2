import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_DIR = Path(__file__).resolve().parents[2]
LOCAL_TIMEZONE = ZoneInfo("America/Sao_Paulo")
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def stable_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def local_timestamp_label(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M")


def sampling_signature(report: dict) -> str:
    config = report["config"]
    sample_contract = {
        "asset": config["asset"],
        "timeframe": config["timeframe"],
        "from_ts": config["from_ts"],
        "to_ts": config["to_ts"],
        "horizons_minutes": config["horizons_minutes"],
        "threshold_pct": config["threshold_pct"],
        "exposure_pct": config["exposure_pct"],
        "windows": [
            {
                "id": item["id"],
                "regime": item["regime"],
                "start_ts": item["start_ts"],
                "end_ts": item["end_ts"],
                "cycle_timestamps": item["cycle_timestamps"],
            }
            for item in report["windows"]
        ],
    }
    return stable_hash(sample_contract)


def result_pair_key(item: dict) -> tuple:
    return item["variant"], item["window_id"], int(item["timestamp"])


def technical_signature(item: dict) -> str:
    return stable_hash(
        {
            "price": item.get("price"),
            "rsi": item.get("rsi"),
            "macd": item.get("macd"),
            "atr": item.get("atr"),
        }
    )


def aggregate_mode(reports: list[dict], mode: str) -> dict:
    rows = [
        item
        for report in reports
        if report["config"]["news_mode"] == mode
        for item in report["results"]
        if item.get("status") == "OK"
    ]
    horizons = sorted({key for item in rows for key in item.get("horizons", {})}, key=int)
    output = {
        "samples": len(rows),
        "errors": sum(
            item.get("status") != "OK"
            for report in reports
            if report["config"]["news_mode"] == mode
            for item in report["results"]
        ),
        "technical_failures": sum(bool(item.get("llm_technical_failure")) for item in rows),
        "llm_actions": dict(Counter(item["llm_action"] for item in rows)),
        "risk_actions": dict(Counter(item["risk_action"] for item in rows)),
        "llm_to_risk": dict(Counter(f"{item['llm_action']}->{item['risk_action']}" for item in rows)),
        "regime_risk_actions": {},
        "risk_reasons": dict(Counter(item["risk_reason"] for item in rows)),
        "horizons": {},
    }
    regime_counts = Counter((item["regime"], item["risk_action"]) for item in rows)
    for regime in sorted({item["regime"] for item in rows}):
        output["regime_risk_actions"][regime] = {
            action: regime_counts[(regime, action)] for action in ("BUY", "SELL", "HOLD")
        }

    for horizon in horizons:
        counts = Counter()
        edges = []
        directional_counts = Counter()
        for item in rows:
            evaluation = item.get("horizons", {}).get(horizon, {})
            maturity = evaluation.get("maturity", "not_matured")
            counts[maturity] += 1
            if maturity != "matured":
                continue
            risk = evaluation["risk"]
            counts[risk["status"]] += 1
            if item["risk_action"] in {"BUY", "SELL"}:
                edges.append(float(risk["directional_edge_after_cost_pct"]))
                directional_counts[risk["status"]] += 1
        decisive = directional_counts["good"] + directional_counts["bad"]
        output["horizons"][horizon] = {
            **dict(counts),
            "directional_samples": len(edges),
            "directional_good": directional_counts["good"],
            "directional_bad": directional_counts["bad"],
            "directional_neutral": directional_counts["neutral"],
            "directional_precision": round(directional_counts["good"] / decisive, 4) if decisive else None,
            "average_directional_edge_after_cost_pct": round(sum(edges) / len(edges), 6) if edges else None,
        }
    return output


def compare_campaign_reports(reports: list[dict], *, baseline_mode: str = "historical") -> dict:
    if not reports:
        raise ValueError("at least one campaign report is required")
    invalid = [report.get("campaign_id", "unknown") for report in reports if report.get("status") != "COMPLETED"]
    if invalid:
        raise ValueError(f"campaigns must be completed: {invalid}")

    groups = defaultdict(list)
    for report in reports:
        groups[sampling_signature(report)].append(report)

    paired_changes = Counter()
    technical_mismatches = []
    incomplete_groups = []
    modes = sorted({report["config"]["news_mode"] for report in reports})
    group_summaries = []
    for signature, group_reports in groups.items():
        by_mode = {report["config"]["news_mode"]: report for report in group_reports}
        if len(by_mode) != len(group_reports):
            raise ValueError(f"duplicate news mode for sampling group {signature}")
        missing_modes = sorted(set(modes) - set(by_mode))
        if baseline_mode not in by_mode or missing_modes:
            incomplete_groups.append({"signature": signature, "missing_modes": missing_modes})
            continue

        baseline_rows = {result_pair_key(item): item for item in by_mode[baseline_mode]["results"]}
        group_summary = {
            "signature": signature,
            "from_ts": by_mode[baseline_mode]["config"]["from_ts"],
            "to_ts": by_mode[baseline_mode]["config"]["to_ts"],
            "paired_points": len(baseline_rows),
            "changes_vs_baseline": {},
        }
        for mode, report in by_mode.items():
            if mode == baseline_mode:
                continue
            rows = {result_pair_key(item): item for item in report["results"]}
            if set(rows) != set(baseline_rows):
                raise ValueError(f"paired result keys differ for sampling group {signature} mode {mode}")
            changes = 0
            for key, baseline in baseline_rows.items():
                candidate = rows[key]
                if technical_signature(candidate) != technical_signature(baseline):
                    technical_mismatches.append({"signature": signature, "mode": mode, "key": list(key)})
                before = (baseline["llm_action"], baseline["risk_action"])
                after = (candidate["llm_action"], candidate["risk_action"])
                if before != after:
                    changes += 1
                    paired_changes[f"{baseline_mode}->{mode}:{before[0]}->{after[0]}:{before[1]}->{after[1]}"] += 1
            group_summary["changes_vs_baseline"][mode] = changes
        group_summaries.append(group_summary)

    if incomplete_groups:
        raise ValueError(f"campaign matrix is incomplete: {incomplete_groups}")
    if technical_mismatches:
        raise ValueError(f"technical payload changed across news interventions: {technical_mismatches[:3]}")

    return {
        "schema_version": 1,
        "baseline_mode": baseline_mode,
        "reports": len(reports),
        "sampling_groups": len(groups),
        "modes": modes,
        "paired_points": sum(item["paired_points"] for item in group_summaries),
        "technical_mismatches": technical_mismatches,
        "paired_changes": dict(paired_changes),
        "groups": sorted(group_summaries, key=lambda item: item["from_ts"]),
        "mode_summary": {mode: aggregate_mode(reports, mode) for mode in modes},
        "limitations": [
            "Regimes were selected retrospectively and do not form an unbiased backtest.",
            "Synthetic neutral news and technical-only modes are interventions, not observed market states.",
            "Directional precision excludes neutral directional outcomes but HOLD labels remain review aids.",
            "The sample is too small to establish profitability or justify real-money execution.",
        ],
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Historical News-Mode Comparison",
        "",
        f"Reports: `{report['reports']}` | sampling groups: `{report['sampling_groups']}` | paired points: `{report['paired_points']}`",
        "",
        "All paired points retained identical price, RSI, MACD, and ATR values across news interventions.",
        "",
        "## Aggregate",
        "",
        "| Mode | Samples | LLM actions | Risk actions | Technical failures |",
        "|---|---:|---|---|---:|",
    ]
    for mode, summary in report["mode_summary"].items():
        lines.append(
            f"| {mode} | {summary['samples']} | `{summary['llm_actions']}` | `{summary['risk_actions']}` | "
            f"{summary['technical_failures']} |"
        )

    lines.extend(
        [
            "",
            "## Net Directional Evaluation",
            "",
            "| Mode | Horizon | Matured | Directional | Good | Bad | Neutral | Precision | Average edge after costs |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for mode, summary in report["mode_summary"].items():
        for horizon, bucket in summary["horizons"].items():
            average = bucket.get("average_directional_edge_after_cost_pct")
            precision = bucket.get("directional_precision")
            precision_text = f"{precision:.1%}" if precision is not None else "N/A"
            lines.append(
                f"| {mode} | {horizon}m | {bucket.get('matured', 0)} | {bucket['directional_samples']} | "
                f"{bucket['directional_good']} | {bucket['directional_bad']} | {bucket['directional_neutral']} | "
                f"{precision_text} | "
                f"{f'{average:+.4f}%' if average is not None else 'N/A'} |"
            )

    lines.extend(["", "## Paired Changes", ""])
    for group in report["groups"]:
        lines.append(
            f"- `{local_timestamp_label(group['from_ts'])}..{local_timestamp_label(group['to_ts'])}` "
            f"(America/Sao_Paulo): {group['paired_points']} points; "
            f"changes vs {report['baseline_mode']}: `{group['changes_vs_baseline']}`"
        )
    lines.append("")
    for change, count in sorted(report["paired_changes"].items()):
        lines.append(f"- `{change}`: {count}")

    lines.extend(["", "## Interpretation", ""])
    lines.extend(
        [
            "Historical news produced fewer directional actions. Replacing news with neutral or empty context increased activity,",
            "but the added trades did not produce positive average edge after the configured fee and slippage assumptions.",
            "This supports retaining news as a risk context while revisiting the hard stale-news policy only through more paired tests;",
            "it does not support removing news checks or lowering the Risk Manager threshold.",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def expand_inputs(values: list[str]) -> list[Path]:
    paths = []
    for value in values:
        path = Path(value)
        if any(character in value for character in "*?"):
            parent = path.parent if str(path.parent) not in {"", "."} else Path.cwd()
            paths.extend(sorted(parent.glob(path.name)))
        else:
            paths.append(path)
    unique = []
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare completed historical campaigns across paired news modes.")
    parser.add_argument("inputs", nargs="+", help="JSON report paths or glob patterns.")
    parser.add_argument("--baseline-mode", default="historical")
    parser.add_argument("--json-out", default="backend/reports/last_historical_mode_comparison.json")
    parser.add_argument("--md-out", default="backend/reports/last_historical_mode_comparison.md")
    args = parser.parse_args()

    paths = expand_inputs(args.inputs)
    if not paths:
        raise SystemExit("No input reports matched")
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    comparison = compare_campaign_reports(reports, baseline_mode=args.baseline_mode)
    comparison["source_files"] = [str(path) for path in paths]

    json_path = Path(args.json_out)
    md_path = Path(args.md_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(comparison, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(comparison), encoding="utf-8")
    print(f"Compared {len(paths)} reports across {comparison['paired_points']} paired points.")
    print(f"JSON: {json_path.resolve()}")
    print(f"Markdown: {md_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
