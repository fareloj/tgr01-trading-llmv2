"""Run resumable multi-agent evaluations over frozen historical BTC/BRL data."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))
load_dotenv(BACKEND_DIR / ".env")

from backend.agents.contracts import MultiAgentDecision, NewsAnalysis, TechnicalAnalysis
from backend.agents.model_config import resolve_multi_agent_model_config
from backend.agents.multi_agent_pipeline import (
    DECISION_SYSTEM_PROMPT,
    NEWS_SYSTEM_PROMPT,
    TECHNICAL_SYSTEM_PROMPT,
    MultiAgentAnalysisPipeline,
    StructuredAgentClient,
)
from backend.evaluation.historical_campaign import classify_action_after_costs
from backend.evaluation.multi_agent_snapshots import (
    build_snapshot,
    load_partition_bars,
    select_stratified_samples,
)
from backend.risk.risk_manager import RiskManager


DEFAULT_MANIFEST = BACKEND_DIR / "data_exports" / "historical_evaluation" / "manifest.json"
REPORTS_DIR = BACKEND_DIR / "reports"


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return round(float(ordered[index]), 3)


def summarize(results: list[dict]) -> dict:
    role_errors = Counter()
    role_latencies: dict[str, list[float]] = {"news": [], "technical": [], "decision": []}
    actions = Counter()
    risk_actions = Counter()
    expected = Counter()
    matched = Counter()
    news_status = Counter()
    technical_regime = Counter()
    evaluation = Counter()
    for item in results:
        for role in role_latencies:
            call = item.get(role, {})
            if call.get("error"):
                role_errors[role] += 1
            elif call.get("latency_ms") is not None:
                role_latencies[role].append(float(call["latency_ms"]))
        news_status[item.get("news", {}).get("output", {}).get("status", "ERROR")] += 1
        technical_regime[item.get("technical", {}).get("output", {}).get("regime", "ERROR")] += 1
        action = item.get("decision", {}).get("output", {}).get("action", "ERROR")
        actions[action] += 1
        risk_actions[item.get("risk", {}).get("action", "ERROR")] += 1
        expected_action = item.get("sample", {}).get("expected_action")
        if expected_action:
            expected[expected_action] += 1
            matched[expected_action] += action == expected_action
        evaluation[item.get("future_evaluation", {}).get("status", "unknown")] += 1
    return {
        "samples": len(results),
        "role_errors": dict(role_errors),
        "latency_ms": {
            role: {
                "mean": round(statistics.fmean(values), 3) if values else None,
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
            }
            for role, values in role_latencies.items()
        },
        "news_status": dict(news_status),
        "technical_regime": dict(technical_regime),
        "llm_actions": dict(actions),
        "risk_actions": dict(risk_actions),
        "future_labels": dict(expected),
        "llm_matches_retrospective_label": dict(matched),
        "future_evaluation": dict(evaluation),
    }


def render_markdown(report: dict) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Multi-Agent Historical Campaign",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Partition: `{report['config']['partition']}`",
        f"- Samples: `{summary.get('samples', 0)}`",
        f"- Models: `{json.dumps(report['config']['models'], ensure_ascii=False)}`",
        "- News: synthetic fixtures, never historical claims.",
        "- Orders: no exchange or paper portfolio writes; Risk Manager verdicts only.",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(summary, indent=2, ensure_ascii=False),
        "```",
        "",
        "Retrospective labels use the future 8-hour move for evaluation only and are not sent to any model.",
    ]
    return "\n".join(lines) + "\n"


def call_with_retry(client, *, retries: int, **kwargs):
    last_error = None
    for attempt in range(retries + 1):
        try:
            return client.call(**kwargs)
        except Exception as error:
            last_error = error
            if attempt < retries:
                time.sleep(min(10, 2 ** attempt))
    raise last_error


def call_record(call) -> dict:
    return {
        "model": call.model,
        "latency_ms": call.latency_ms,
        "output": call.output.model_dump(),
    }


def sample_completed(record: dict) -> bool:
    """Only fully validated role chains are complete enough to resume past."""
    return all(
        isinstance(record.get(role), dict) and not record[role].get("error")
        for role in ("news", "technical", "decision")
    )


def run_sample(snapshot: dict, sample: dict, pipeline, risk_manager, retries: int) -> dict:
    record = {"sample": {key: value for key, value in sample.items() if key != "bar_index"}}
    news_input = {
        "news_context": snapshot["news_context"],
        "data_health": snapshot["data_health"],
        "deterministic_news_risk": snapshot["news_risk"],
    }
    try:
        news = call_with_retry(
            pipeline.client,
            retries=retries,
            role="news",
            model=pipeline.config.news_model,
            system_prompt=NEWS_SYSTEM_PROMPT,
            payload=news_input,
            schema=NewsAnalysis,
        )
        pipeline._validate_news_evidence(news.output, news_input["news_context"])
        record["news"] = call_record(news)
        news_report = news.output
    except Exception as error:
        record["news"] = {"model": pipeline.config.news_model, "error": f"{type(error).__name__}: {str(error)[:240]}"}
        news_report = NewsAnalysis(
            status="DEGRADED", bias="UNCERTAIN", confidence=0,
            summary="News agent failed; no news evidence accepted.", gaps=["agent_failure"],
        )
    news_call_failed = bool(record["news"].get("error"))

    technical_input = {
        "technical_context": snapshot["technical_context"],
        "data_health": snapshot["data_health"],
        "news_report": news_report.model_dump(),
    }
    try:
        technical = call_with_retry(
            pipeline.client,
            retries=retries,
            role="technical",
            model=pipeline.config.technical_model,
            system_prompt=TECHNICAL_SYSTEM_PROMPT,
            payload=technical_input,
            schema=TechnicalAnalysis,
        )
        pipeline._validate_technical_evidence(technical.output, technical_input["technical_context"])
        record["technical"] = call_record(technical)
        technical_report = technical.output
    except Exception as error:
        record["technical"] = {"model": pipeline.config.technical_model, "error": f"{type(error).__name__}: {str(error)[:240]}"}
        technical_report = TechnicalAnalysis(
            status="DEGRADED", regime="INSUFFICIENT_DATA", direction="UNCERTAIN",
            confidence=0, summary="Technical agent failed; directional evidence rejected.",
            news_alignment="UNAVAILABLE", invalidation_conditions=["agent_failure"],
        )
    technical_call_failed = bool(record["technical"].get("error"))

    decision_input = {
        "original_snapshot": snapshot,
        "news_report": news_report.model_dump(),
        "technical_report": technical_report.model_dump(),
    }
    try:
        decision = call_with_retry(
            pipeline.client,
            retries=retries,
            role="decision",
            model=pipeline.config.decision_model,
            system_prompt=DECISION_SYSTEM_PROMPT,
            payload=decision_input,
            schema=MultiAgentDecision,
        )
        pipeline._validate_decision(decision.output, snapshot, news_report, technical_report)
        if news_call_failed or technical_call_failed or technical_report.status != "OK":
            if decision.output.action != "HOLD":
                raise ValueError("directional action after an upstream agent failure")
        record["decision"] = call_record(decision)
        decision_report = decision.output
    except Exception as error:
        record["decision"] = {"model": pipeline.config.decision_model, "error": f"{type(error).__name__}: {str(error)[:240]}"}
        decision_report = MultiAgentDecision(
            action="HOLD", conviction=0, thesis="Agent failure; fail-closed HOLD.",
            invalidation_conditions=["agent_failure"],
        )

    risk = risk_manager.evaluate_order(
        llm_action=decision_report.action,
        llm_conviction=decision_report.conviction,
        payload=snapshot,
        current_exposure=snapshot["portfolio_context"]["current_exposure_percentage"],
    )
    record["risk"] = risk
    record["future_evaluation"] = classify_action_after_costs(
        risk["action"],
        raw_move_pct=float(sample["future_move_8h_pct"]),
        buy_cost_pct=0.7,
        sell_cost_pct=0.35,
        threshold_pct=0.2,
    )
    record["fixture"] = snapshot["evaluation_context"]
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate all multi-agent roles on frozen historical snapshots.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--partition", choices=["development", "validation"], required=True)
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.samples <= 0 or args.samples > 500:
        raise SystemExit("--samples must be between 1 and 500")
    bars, manifest = load_partition_bars(args.manifest, args.partition)
    samples = select_stratified_samples(bars, args.samples)
    config = resolve_multi_agent_model_config()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output or REPORTS_DIR / f"multi_agent_{args.partition}_{args.samples}_{timestamp}.json"
    markdown = output.with_suffix(".md")
    descriptor = {
        "partition": args.partition,
        "samples": args.samples,
        "dataset_id": manifest["dataset_id"],
        "models": {
            "news": config.news_model,
            "technical": config.technical_model,
            "decision": config.decision_model,
        },
        "prompt_hashes": {
            "news": prompt_hash(NEWS_SYSTEM_PROMPT),
            "technical": prompt_hash(TECHNICAL_SYSTEM_PROMPT),
            "decision": prompt_hash(DECISION_SYSTEM_PROMPT),
        },
        "synthetic_news_fixtures": True,
        "future_label_not_in_model_input": True,
        "writes_trading_state": False,
    }
    if args.resume and output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
        if report.get("config") != descriptor:
            raise SystemExit("resume output does not match current models/prompts/dataset")
        report["results"] = [item for item in report.get("results", []) if sample_completed(item)]
    else:
        report = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "RUNNING",
            "config": descriptor,
            "results": [],
            "summary": {},
        }
    completed = {
        int(item["sample"]["timestamp"])
        for item in report["results"]
        if sample_completed(item)
    }
    pipeline = MultiAgentAnalysisPipeline(config=config, client=StructuredAgentClient(config))
    risk_manager = RiskManager(max_exposure=80.0, cooldown_minutes=0)
    for index, sample in enumerate(samples):
        if int(sample["timestamp"]) in completed:
            continue
        print(f"[{len(report['results']) + 1}/{args.samples}] {sample['outcome_regime']} ts={sample['timestamp']}", flush=True)
        snapshot = build_snapshot(bars, sample, index)
        report["results"].append(run_sample(snapshot, sample, pipeline, risk_manager, args.retries))
        report["summary"] = summarize(report["results"])
        atomic_write(output, report)
        markdown.write_text(render_markdown(report), encoding="utf-8")
    report["status"] = "COMPLETED" if not any(report["summary"]["role_errors"].values()) else "COMPLETED_WITH_ERRORS"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = summarize(report["results"])
    atomic_write(output, report)
    markdown.write_text(render_markdown(report), encoding="utf-8")
    print(f"Status: {report['status']}; output={output}")
    return 0 if report["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
