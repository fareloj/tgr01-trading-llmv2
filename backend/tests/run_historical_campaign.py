import argparse
import hashlib
import inspect
import json
import os
import sys
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
REPORTS_DIR = BACKEND_DIR / "reports"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.agents.decision_agent import (
    DecisionAgent,
    enforce_payload_decision_constraints,
    has_llm_api_key,
    replace_generic_hold_reason,
    parse_retry_seconds,
)
import backend.agents.decision_agent as decision_agent_module
from backend.core import database
from backend.evaluation.historical_campaign import (
    campaign_fingerprint,
    evaluate_result_horizons,
    freeze_windows,
    select_non_overlapping_windows,
    summarize_results,
)
from backend.evaluation.historical_dataset import verify_manifest_contract
from backend.execution.paper_simulator import PaperExecutionConfig, estimate_slippage_rate
from backend.features.payload_builder import build_agent_payload
from backend.risk.risk_manager import RiskManager
from backend.tests.compare_prompt_profiles import PROMPT_PROFILES, PromptProfileRunner
from backend.tests.find_market_windows import fetch_candles, find_windows, parse_local_datetime

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


def format_local(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def parse_csv_ints(value: str) -> list[int]:
    values = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("use positive comma-separated integers")
    return values


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def variant_descriptors(variants: list[str]) -> list[dict]:
    descriptors = []
    model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    for variant in variants:
        if variant == "current":
            source = inspect.getsource(DecisionAgent.evaluate_market)
            descriptors.append(
                {
                    "name": variant,
                    "kind": "production",
                    "provider": "groq",
                    "model": model,
                    "prompt_sha256": sha256_text(source),
                }
            )
        else:
            descriptors.append(
                {
                    "name": variant,
                    "kind": "prompt_profile",
                    "provider": "groq",
                    "model": model,
                    "prompt_sha256": sha256_text(PROMPT_PROFILES[variant]),
                }
            )
    return descriptors


def apply_news_mode(payload: dict, mode: str, timestamp: int) -> None:
    if mode == "historical":
        payload["news_context_mode"] = "OBSERVED"
        return
    health = payload.setdefault("data_health", {})
    risk = payload.setdefault("news_risk", {})
    risk.update(
        {
            "has_negative_red_flag": False,
            "has_untrusted_instruction": False,
            "risk_level": "NORMAL",
            "matched_terms": [],
            "matched_headlines": [],
        }
    )
    if mode == "neutral-fresh":
        payload["news_context_mode"] = "SYNTHETIC_NEUTRAL"
        health["latest_news_timestamp"] = timestamp
        health["is_news_stale"] = False
        health["news_age_seconds"] = 0
        payload["news_context"] = [
            {
                "timestamp": timestamp,
                "headline": "Neutral historical evaluation context.",
                "source": "Synthetic campaign fixture",
            }
        ]
    elif mode == "technical-only":
        payload["news_context_mode"] = "UNAVAILABLE_BY_TEST_DESIGN"
        health["latest_news_timestamp"] = None
        health["is_news_stale"] = True
        health["news_age_seconds"] = None
        risk["risk_level"] = "UNAVAILABLE"
        payload["news_context"] = []
        payload["test_mode_instructions"] = (
            "Historical intervention: news is unavailable by test design. Evaluate technical indicators "
            "and exposure without news direction. Never describe news as fresh, current, mixed, positive, "
            "or negative."
        )


def atomic_write_json(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def render_markdown(report: dict) -> str:
    config = report["config"]
    dataset_lines = []
    if config.get("dataset_id"):
        dataset_lines = [
            f"- Dataset: `{config['dataset_id']}`",
            f"- Dataset partition: `{config['dataset_partition']}`",
            f"- Selection strategy: `{config.get('selection_strategy', 'extreme')}`",
        ]
    lines = [
        "# Historical Decision Campaign",
        "",
        f"Campaign: `{report['campaign_id']}`",
        f"Status: `{report['status']}`",
        f"Database: `{report['database']}`",
        "",
        "> This is a stratified, retrospective evaluation. Regimes are selected using the full-window outcome,",
        "> so results measure behavior under known conditions and must not be interpreted as an unbiased backtest.",
        "",
        "No portfolio balance, order, or trade log is changed by this campaign.",
        "",
        "## Configuration",
        "",
        f"- Range: `{format_local(config['from_ts'])}` to `{format_local(config['to_ts'])}`",
        *dataset_lines,
        f"- Variants: `{', '.join(config['variants'])}`",
        f"- News mode: `{config['news_mode']}`",
        f"- Frozen exposure: `{config['exposure_pct']}%`",
        f"- Horizons: `{config['horizons_minutes']}` minutes",
        f"- Decision threshold: `{config['threshold_pct']}%` after estimated costs",
        f"- Fee assumption: `{config['execution_costs']['fee_rate'] * 100:.4f}%` per side",
        "",
        "## Frozen Windows",
        "",
        "| ID | Regime | Local window | Move | Volatility | Cycles | Expected |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for window in report["windows"]:
        lines.append(
            f"| {window['id']} | {window['regime']} | {format_local(window['start_ts'])} to "
            f"{format_local(window['end_ts'])} | {window['move_pct']:+.4f}% | "
            f"{window['volatility_pct']:.4f}% | {len(window['cycle_timestamps'])} | "
            f"{window['expected_action'] or 'N/A'} |"
        )

    lines.extend(["", "## Summary", ""])
    if not report.get("summary"):
        lines.append("No LLM results were executed. This is a plan-only report.")
    for variant, data in report.get("summary", {}).items():
        lines.extend(
            [
                f"### {variant}",
                "",
                f"- Samples: `{data['samples']}`; errors: `{data['errors']}`",
                f"- LLM actions: `{data['llm_actions']}`",
                f"- Risk actions: `{data['risk_actions']}`",
                f"- LLM to Risk: `{data['llm_to_risk']}`",
                "",
                "| Regime | Expected | Samples | LLM matches | Risk matches |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for regime, alignment in data["regime_alignment"].items():
            lines.append(
                f"| {regime} | {alignment['expected_action'] or 'N/A'} | {alignment['samples']} | "
                f"{alignment['llm_matches'] if alignment['llm_matches'] is not None else 'N/A'} | "
                f"{alignment['risk_matches'] if alignment['risk_matches'] is not None else 'N/A'} |"
            )
        lines.extend(
            [
                "",
                "| Horizon | Matured | Gaps | Directional | D-good | D-bad | D-neutral | Precision | Avg net edge | Missed upside | Avoided downside |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for horizon, bucket in data["horizons"].items():
            average = bucket.get("average_directional_edge_after_cost_pct")
            precision = bucket.get("directional_precision")
            precision_text = f"{precision:.1%}" if precision is not None else "N/A"
            lines.append(
                f"| {horizon}m | {bucket.get('matured', 0)} | {bucket.get('data_gap', 0)} | "
                f"{bucket['directional_samples']} | {bucket['directional_good']} | {bucket['directional_bad']} | "
                f"{bucket['directional_neutral']} | {precision_text} | "
                f"{f'{average:+.4f}%' if average is not None else 'N/A'} | "
                f"{bucket.get('missed_upside', 0)} | {bucket.get('avoided_downside', 0)} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Interpretation Guardrails",
            "",
            "- SELL is scored as avoided loss while reducing an existing BTC long exposure; it is not short PnL.",
            "- BUY uses round-trip costs; SELL uses one exit cost because it reduces an existing long position.",
            "- Size-weighted edge is a comparison proxy, not a reconstructed portfolio return.",
            "- Synthetic or technical-only news modes are explicit interventions and cannot validate news performance.",
            "- A useful conclusion requires enough matured BUY and SELL samples across multiple non-overlapping periods.",
            "",
        ]
    )
    return "\n".join(lines)


def result_key(item: dict) -> str:
    return f"{item['variant']}:{item['window_id']}:{item['timestamp']}"


def run_decision(variant: str, payload: dict, current_agent, profile_runner):
    if variant == "current":
        return current_agent.evaluate_market(payload)
    decision = profile_runner.run(variant, payload)
    decision = replace_generic_hold_reason(decision, payload)
    return enforce_payload_decision_constraints(decision, payload)


def run_decision_with_retry(
    variant: str,
    payload: dict,
    current_agent,
    profile_runner,
    *,
    retries: int,
    minimum_wait_seconds: float,
    sleep_fn=time.sleep,
):
    """Retry bounded provider failures while preserving fail-closed decisions."""
    last_decision = None
    for attempt in range(retries + 1):
        try:
            decision = run_decision(variant, payload, current_agent, profile_runner)
        except Exception as error:
            if type(error).__name__ != "RateLimitError" or attempt >= retries:
                raise
            wait_seconds = max(minimum_wait_seconds, parse_retry_seconds(error) + 1)
            print(f"[retry] Rate limit em {variant}; aguardando {wait_seconds:.0f}s.")
            sleep_fn(wait_seconds)
            continue

        last_decision = decision
        technical_failure = "technical failure" in decision.reasoning.lower()
        if not technical_failure or attempt >= retries:
            return decision

        cooldown_remaining = max(0, decision_agent_module.LLM_COOLDOWN_UNTIL - int(time.time()))
        wait_seconds = max(minimum_wait_seconds, cooldown_remaining + 1)
        print(f"[retry] Falha tecnica em {variant}; aguardando {wait_seconds:.0f}s.")
        sleep_fn(wait_seconds)
    return last_decision


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze market regimes and compare LLM/Risk decisions without executing paper orders."
    )
    parser.add_argument("--from-local", help='Start in "YYYY-MM-DD HH:MM".')
    parser.add_argument("--to-local", help='End in "YYYY-MM-DD HH:MM".')
    parser.add_argument("--dataset-manifest", help="Frozen historical evaluation manifest.")
    parser.add_argument("--partition", choices=["development", "validation", "holdout"])
    parser.add_argument(
        "--holdout-approval",
        help="To evaluate holdout, pass the exact dataset_id after finalizing all candidate rules.",
    )
    parser.add_argument("--asset", default="BTC/BRL")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--variants", nargs="+", choices=["current", *sorted(PROMPT_PROFILES)], default=["current", "balanced"])
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--stride-minutes", type=int, default=10)
    parser.add_argument("--per-regime", type=int, default=1)
    parser.add_argument("--selection-strategy", choices=["stratified", "extreme"], default="stratified")
    parser.add_argument("--include-high-volatility", action="store_true")
    parser.add_argument("--cycles-per-window", type=int, default=5)
    parser.add_argument("--step-seconds", type=int, default=300)
    parser.add_argument("--trend-threshold-pct", type=float, default=0.5)
    parser.add_argument("--sideways-threshold-pct", type=float, default=0.15)
    parser.add_argument("--min-coverage-pct", type=float, default=80.0)
    parser.add_argument("--horizons", type=parse_csv_ints, default=[5, 15, 30, 60])
    parser.add_argument("--max-candle-delay", type=int, default=90)
    parser.add_argument("--threshold-pct", type=float, default=0.20)
    parser.add_argument("--exposure-pct", type=float, default=40.0)
    parser.add_argument("--news-mode", choices=["historical", "neutral-fresh", "technical-only"], default="historical")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--max-calls", type=int, default=100)
    parser.add_argument("--llm-retries", type=int, default=2)
    parser.add_argument("--retry-wait-seconds", type=float, default=5.0)
    parser.add_argument("--yes", action="store_true", help="Allow a campaign larger than --max-calls.")
    parser.add_argument("--resume", action="store_true", help="Resume an existing --json-out with the same fingerprint.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    return parser


def resolve_campaign_range(args) -> tuple[int, int, dict | None]:
    if bool(args.dataset_manifest) != bool(args.partition):
        raise SystemExit("--dataset-manifest and --partition must be provided together")
    if not args.dataset_manifest:
        if not args.from_local or not args.to_local:
            raise SystemExit("Provide --from-local/--to-local or a frozen dataset manifest and partition")
        return parse_local_datetime(args.from_local), parse_local_datetime(args.to_local), None

    manifest = json.loads(Path(args.dataset_manifest).read_text(encoding="utf-8"))
    try:
        verify_manifest_contract(manifest)
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(f"Invalid historical dataset manifest: {error}") from error
    dataset_id = str(manifest.get("dataset_id") or "")
    if not dataset_id or args.partition not in manifest.get("partitions", {}):
        raise SystemExit("Invalid historical dataset manifest")
    if args.partition == "holdout" and args.holdout_approval != dataset_id:
        raise SystemExit(
            f"Holdout is sealed. Pass --holdout-approval {dataset_id} only after prompts and rules are frozen."
        )
    bounds = manifest["partitions"][args.partition]
    from_ts = parse_local_datetime(args.from_local) if args.from_local else int(bounds["start_timestamp"])
    to_ts = parse_local_datetime(args.to_local) if args.to_local else int(bounds["end_timestamp"])
    if from_ts < int(bounds["start_timestamp"]) or to_ts > int(bounds["end_timestamp"]):
        raise SystemExit(f"Requested range escapes the frozen {args.partition} partition")
    return from_ts, to_ts, manifest


def main() -> int:
    args = build_parser().parse_args()
    from_ts, to_ts, dataset_manifest = resolve_campaign_range(args)
    if from_ts >= to_ts:
        raise SystemExit("--from-local must be earlier than --to-local")
    if not 0 <= args.exposure_pct <= 100:
        raise SystemExit("--exposure-pct must be between 0 and 100")
    positive_arguments = {
        "--window-minutes": args.window_minutes,
        "--stride-minutes": args.stride_minutes,
        "--per-regime": args.per_regime,
        "--cycles-per-window": args.cycles_per_window,
        "--step-seconds": args.step_seconds,
        "--max-candle-delay": args.max_candle_delay,
        "--max-calls": args.max_calls,
    }
    invalid_positive = [name for name, value in positive_arguments.items() if value <= 0]
    if invalid_positive:
        raise SystemExit(f"These arguments must be positive: {', '.join(invalid_positive)}")
    if args.threshold_pct < 0 or args.trend_threshold_pct <= 0 or args.sideways_threshold_pct < 0:
        raise SystemExit("Decision/market thresholds must be non-negative and trend threshold must be positive")
    if not 0 < args.min_coverage_pct <= 100:
        raise SystemExit("--min-coverage-pct must be in (0, 100]")
    if args.llm_retries < 0 or args.retry_wait_seconds < 0:
        raise SystemExit("--llm-retries and --retry-wait-seconds cannot be negative")

    source_candles = fetch_candles(args.asset, args.timeframe, from_ts, to_ts)
    candidates = find_windows(
        source_candles,
        window_minutes=args.window_minutes,
        stride_minutes=args.stride_minutes,
        trend_threshold_pct=args.trend_threshold_pct,
        sideways_threshold_pct=args.sideways_threshold_pct,
        min_coverage_pct=args.min_coverage_pct,
    )
    selected = select_non_overlapping_windows(
        candidates,
        per_regime=args.per_regime,
        include_high_volatility=args.include_high_volatility,
        strategy=args.selection_strategy,
    )
    candle_timestamps = [int(item["timestamp"]) for item in source_candles]
    frozen = freeze_windows(
        selected,
        candle_timestamps,
        cycles=args.cycles_per_window,
        step_seconds=args.step_seconds,
    )
    regime_counts = Counter(item.regime for item in frozen)
    incomplete = [
        regime
        for regime in ("UPTREND", "DOWNTREND", "SIDEWAYS")
        if regime_counts[regime] < args.per_regime
    ]
    if incomplete:
        raise SystemExit(f"Could not freeze {args.per_regime} windows for regimes: {', '.join(incomplete)}")
    if any(not item.cycle_timestamps for item in frozen):
        raise SystemExit("At least one selected window has no cycle timestamps")

    execution_config = PaperExecutionConfig.from_env()
    config = {
        "asset": args.asset,
        "timeframe": args.timeframe,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "variants": args.variants,
        "window_minutes": args.window_minutes,
        "stride_minutes": args.stride_minutes,
        "per_regime": args.per_regime,
        "selection_strategy": args.selection_strategy,
        "include_high_volatility": args.include_high_volatility,
        "cycles_per_window": args.cycles_per_window,
        "step_seconds": args.step_seconds,
        "horizons_minutes": args.horizons,
        "max_candle_delay_seconds": args.max_candle_delay,
        "threshold_pct": args.threshold_pct,
        "news_mode": args.news_mode,
        "exposure_pct": args.exposure_pct,
        "execution_costs": {
            "fee_rate": execution_config.fee_rate,
            "min_slippage_rate": execution_config.min_slippage_rate,
            "max_slippage_rate": execution_config.max_slippage_rate,
            "atr_slippage_factor": execution_config.atr_slippage_factor,
        },
        "llm_retries": args.llm_retries,
        "retry_wait_seconds": args.retry_wait_seconds,
        "dataset_id": dataset_manifest.get("dataset_id") if dataset_manifest else None,
        "dataset_partition": args.partition if dataset_manifest else None,
    }
    descriptors = variant_descriptors(args.variants)
    fingerprint = campaign_fingerprint(config, frozen, descriptors)
    timestamp_label = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = Path(args.json_out) if args.json_out else REPORTS_DIR / f"historical_campaign_{timestamp_label}.json"
    md_path = Path(args.md_out) if args.md_out else json_path.with_suffix(".md")

    planned_calls = sum(len(item.cycle_timestamps) for item in frozen) * len(args.variants)
    if planned_calls > args.max_calls and not args.yes and not args.plan_only:
        raise SystemExit(
            f"Campaign plans {planned_calls} LLM calls, above --max-calls={args.max_calls}. "
            "Review with --plan-only or pass --yes explicitly."
        )
    if json_path.exists() and not args.resume and not args.overwrite:
        raise SystemExit(f"Output already exists: {json_path}. Use --resume or --overwrite.")

    if args.resume:
        if not json_path.exists():
            raise SystemExit("--resume requires an existing --json-out")
        report = json.loads(json_path.read_text(encoding="utf-8"))
        if report.get("campaign_id") != fingerprint:
            raise SystemExit("Existing report fingerprint does not match the frozen campaign")
        report["results"] = [
            item
            for item in report.get("results", [])
            if item.get("status") == "OK" and not item.get("llm_technical_failure")
        ]
    else:
        report = {
            "schema_version": 1,
            "campaign_id": fingerprint,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "PLAN_ONLY" if args.plan_only else "RUNNING",
            "database": database.get_database_label(),
            "config": config,
            "variants": descriptors,
            "selection_bias_warning": (
                "Regimes were selected retrospectively using full-window outcomes. "
                "This is stratified behavior evaluation, not an unbiased backtest."
            ),
            "planned_llm_calls": planned_calls,
            "windows": [item.to_dict() for item in frozen],
            "results": [],
            "summary": {},
        }

    atomic_write_json(json_path, report)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Campaign: {fingerprint}")
    print(f"Frozen windows: {len(frozen)}; planned LLM calls: {planned_calls}")
    print(f"JSON: {json_path.resolve()}")
    print(f"Markdown: {md_path.resolve()}")
    if args.plan_only:
        return 0
    if not has_llm_api_key():
        raise SystemExit("No configured LLM key. The frozen plan was saved; add a key and rerun with --resume.")

    max_horizon_seconds = max(args.horizons) * 60 + args.max_candle_delay
    evaluation_candles = fetch_candles(
        args.asset,
        args.timeframe,
        min(item.start_ts for item in frozen),
        max(item.end_ts for item in frozen) + max_horizon_seconds,
    )
    current_agent = DecisionAgent() if "current" in args.variants else None
    profile_runner = PromptProfileRunner() if any(item != "current" for item in args.variants) else None
    risk_manager = RiskManager(max_exposure=80.0, cooldown_minutes=0)
    completed = {result_key(item) for item in report["results"]}

    for variant in args.variants:
        for window in frozen:
            for cycle_index, decision_timestamp in enumerate(window.cycle_timestamps, start=1):
                key = f"{variant}:{window.id}:{decision_timestamp}"
                if key in completed:
                    continue
                print(f"[{variant}] {window.id} cycle {cycle_index}/{len(window.cycle_timestamps)} {format_local(decision_timestamp)}")
                item = {
                    "variant": variant,
                    "window_id": window.id,
                    "regime": window.regime,
                    "expected_action": window.expected_action,
                    "cycle": cycle_index,
                    "timestamp": decision_timestamp,
                    "local_time": format_local(decision_timestamp),
                }
                try:
                    payload = build_agent_payload(
                        asset=args.asset,
                        timeframe=args.timeframe,
                        as_of_timestamp=decision_timestamp,
                    )
                    if payload.get("status") == "ERROR":
                        raise RuntimeError(f"payload_error: {payload.get('message', 'unknown')}")
                    apply_news_mode(payload, args.news_mode, decision_timestamp)
                    portfolio = payload.setdefault("portfolio_context", {})
                    portfolio["current_exposure_percentage"] = args.exposure_pct
                    decision = run_decision_with_retry(
                        variant,
                        payload,
                        current_agent,
                        profile_runner,
                        retries=args.llm_retries,
                        minimum_wait_seconds=args.retry_wait_seconds,
                    )
                    final_order = risk_manager.evaluate_order(
                        llm_action=decision.action,
                        llm_conviction=decision.conviction,
                        payload=payload,
                        current_exposure=args.exposure_pct,
                    )
                    technical = payload.get("technical_context", {})
                    slippage_rate = estimate_slippage_rate(payload, execution_config)
                    item.update(
                        {
                            "status": "OK",
                            "price": technical.get("current_price"),
                            "rsi": technical.get("rsi"),
                            "macd": technical.get("macd"),
                            "atr": technical.get("volatility_atr"),
                            "data_health": payload.get("data_health"),
                            "news_risk": payload.get("news_risk"),
                            "llm_action": decision.action,
                            "llm_conviction": decision.conviction,
                            "llm_reasoning": decision.reasoning,
                            "llm_decision_brief": decision.decision_brief,
                            "llm_technical_failure": "technical failure" in decision.reasoning.lower(),
                            "risk_action": final_order["action"],
                            "risk_reason": final_order["reason"],
                            "executed_size": final_order["executed_size"],
                            "fee_rate": execution_config.fee_rate,
                            "slippage_rate": slippage_rate,
                        }
                    )
                    item["horizons"] = evaluate_result_horizons(
                        item,
                        evaluation_candles,
                        horizons=args.horizons,
                        threshold_pct=args.threshold_pct,
                        fee_rate=execution_config.fee_rate,
                        slippage_rate=slippage_rate,
                        max_delay_seconds=args.max_candle_delay,
                    )
                except Exception as error:
                    item.update({"status": "ERROR", "error_type": type(error).__name__, "error": str(error)[:500]})
                report["results"].append(item)
                report["summary"] = summarize_results(report["results"], args.horizons)
                atomic_write_json(json_path, report)
                md_path.write_text(render_markdown(report), encoding="utf-8")

    has_errors = any(item["status"] != "OK" for item in report["results"])
    has_llm_failures = any(item.get("llm_technical_failure") for item in report["results"])
    if has_errors:
        report["status"] = "COMPLETED_WITH_ERRORS"
    elif has_llm_failures:
        report["status"] = "COMPLETED_WITH_LLM_FAILURES"
    else:
        report["status"] = "COMPLETED"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = summarize_results(report["results"], args.horizons)
    atomic_write_json(json_path, report)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Status: {report['status']}; results: {len(report['results'])}")
    return 0 if report["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
