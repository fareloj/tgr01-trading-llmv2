import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BACKEND_DIR / "reports"
sys.path.insert(0, str(BACKEND_DIR.parent))
load_dotenv(BACKEND_DIR / ".env")

from backend.agents.contracts import AnalysisPlan, DecisionOutput
from backend.agents.decision_agent import enforce_payload_decision_constraints, replace_generic_hold_reason
from backend.agents.decision_agent import parse_retry_seconds
from backend.agents.prompt_profiles import SYSTEM_PROMPT_PROFILES
from backend.analysis.tool_engine import DeterministicToolEngine
from backend.features.payload_builder import build_agent_payload
from backend.risk.risk_manager import RiskManager
from backend.tests.compare_llm_models import extract_json_object, parse_model_spec, provider_client
from backend.tests.run_historical_llm_scenarios import fetch_cycle_timestamps


LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
SCENARIOS = {
    "uptrend": ("2026-06-06 01:40", "2026-06-06 02:40"),
    "downtrend": ("2026-06-06 06:40", "2026-06-06 07:40"),
    "sideways": ("2026-06-06 16:00", "2026-06-06 17:00"),
}
FIXED_PLAN = AnalysisPlan.model_validate(
    {
        "requests": [
            {"tool": "multi_timeframe_trend", "windows_minutes": [15, 60, 240]},
            {"tool": "donchian_breakout", "lookback_candles": 20},
            {"tool": "drawdown_profile", "lookback_minutes": 240},
        ],
        "rationale": "Benchmark fixo para comparabilidade entre modelos e prompts.",
    }
)


def _timestamp(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ).timestamp())


def _future_price(asset: str, timeframe: str, timestamp: int, horizon_minutes: int) -> float | None:
    from backend.features.indicators import get_historical_klines

    rows = get_historical_klines(
        asset=asset,
        timeframe=timeframe,
        limit=1,
        as_of_timestamp=timestamp + horizon_minutes * 60,
    )
    if rows.empty:
        return None
    future_timestamp = int(rows.iloc[-1]["timestamp"])
    if future_timestamp < timestamp + (horizon_minutes - 2) * 60:
        return None
    return float(rows.iloc[-1]["close"])


def _score(action: str, move_pct: float | None, threshold: float = 0.2) -> str:
    if move_pct is None:
        return "NOT_MATURED"
    if action == "BUY":
        return "GOOD" if move_pct >= threshold else "BAD" if move_pct <= -threshold else "NEUTRAL"
    if action == "SELL":
        return "GOOD" if move_pct <= -threshold else "BAD" if move_pct >= threshold else "NEUTRAL"
    if abs(move_pct) < threshold:
        return "GOOD"
    return "MISSED_UPSIDE" if move_pct > 0 else "AVOIDED_DOWNSIDE"


def _messages(profile: str, payload: dict) -> list[dict]:
    schema = json.dumps(DecisionOutput.model_json_schema())
    return [
        {"role": "system", "content": SYSTEM_PROMPT_PROFILES[profile]},
        {
            "role": "user",
            "content": f"Schema obrigatorio:\n{schema}\n\nPayload historico:\n{json.dumps(payload, ensure_ascii=False)}",
        },
    ]


def _call(provider: str, model: str, profile: str, payload: dict) -> dict:
    client, error = provider_client(provider)
    if client is None:
        return {"status": "SKIPPED", "error": error}
    client = client.with_options(timeout=90.0, max_retries=0)
    request = {
        "model": model,
        "messages": _messages(profile, payload),
        "temperature": 0.0,
    }
    if model.startswith("openai/gpt-oss"):
        request["max_completion_tokens"] = 3000
        request["reasoning_effort"] = "low"
    elif model == "qwen/qwen3.6-27b":
        request["max_completion_tokens"] = 600
        request["reasoning_effort"] = "none"
        request["extra_body"] = {"reasoning_format": "hidden"}
    else:
        request["max_tokens"] = 300
    started = time.perf_counter()
    try:
        waited_seconds = 0
        attempts = 0
        while True:
            attempts += 1
            try:
                try:
                    response = client.chat.completions.create(
                        **request,
                        response_format={"type": "json_object"},
                    )
                    raw = response.choices[0].message.content or ""
                except Exception as json_error:
                    if "json_validate_failed" not in str(json_error):
                        raise
                    response = client.chat.completions.create(**request)
                    raw = extract_json_object(response.choices[0].message.content or "")
                break
            except Exception as request_error:
                if type(request_error).__name__ != "RateLimitError" or attempts >= 2:
                    raise
                retry_seconds = min(60, parse_retry_seconds(request_error, default_seconds=10) + 1)
                print(f"  rate limit; aguardando {retry_seconds}s para uma unica repeticao", flush=True)
                time.sleep(retry_seconds)
                waited_seconds += retry_seconds
        decision = DecisionOutput.model_validate_json(raw)
        decision = replace_generic_hold_reason(decision, payload)
        decision = enforce_payload_decision_constraints(decision, payload)
        return {
            "status": "OK",
            "latency_seconds": round(time.perf_counter() - started, 3),
            "rate_limit_wait_seconds": waited_seconds,
            "decision": decision.model_dump(),
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "latency_seconds": round(time.perf_counter() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def run(args) -> dict:
    engine = DeterministicToolEngine(audit=False, persist_events=False)
    risk = RiskManager(max_exposure=80.0, cooldown_minutes=0)
    rows = []
    for scenario in args.scenarios:
        start, end = SCENARIOS[scenario]
        all_timestamps = fetch_cycle_timestamps(
            asset=args.asset,
            timeframe=args.timeframe,
            from_ts=_timestamp(start),
            to_ts=_timestamp(end),
            cycles=10_000,
            step_seconds=60,
        )
        if args.cycles == 1:
            timestamps = [all_timestamps[len(all_timestamps) // 2]] if all_timestamps else []
        else:
            indexes = [round(index * (len(all_timestamps) - 1) / (args.cycles - 1)) for index in range(args.cycles)] if all_timestamps else []
            timestamps = [all_timestamps[index] for index in indexes]
        for cycle, timestamp in enumerate(timestamps, start=1):
            base_payload = build_agent_payload(args.asset, args.timeframe, as_of_timestamp=timestamp)
            if base_payload.get("status") == "ERROR":
                rows.append({"scenario": scenario, "cycle": cycle, "timestamp": timestamp, "status": "PAYLOAD_ERROR"})
                continue
            tool_results = engine.execute_plan(
                FIXED_PLAN,
                asset=args.asset,
                timeframe=args.timeframe,
                as_of_timestamp=timestamp,
            )
            payload = json.loads(json.dumps(base_payload))
            payload["deterministic_tool_context"] = {
                "schema_version": 1,
                "status": "OK" if all(item.status == "OK" for item in tool_results) else "DEGRADED",
                "results": [item.model_dump() for item in tool_results],
                "rules": {"only_status_ok_is_evidence": True, "tools_do_not_approve_orders": True},
            }
            entry = float(payload["technical_context"]["current_price"])
            future = {}
            for horizon in (15, 60):
                price = _future_price(args.asset, args.timeframe, timestamp, horizon)
                future[str(horizon)] = {
                    "price": price,
                    "move_pct": round(((price / entry) - 1.0) * 100.0, 4) if price is not None and entry else None,
                }
            for model_spec in args.models:
                provider, model = parse_model_spec(model_spec)
                for profile in args.profiles:
                    print(f"[{scenario} {cycle}] {provider}:{model} / {profile}", flush=True)
                    outcome = _call(provider, model, profile, payload)
                    row = {
                        "scenario": scenario,
                        "cycle": cycle,
                        "timestamp": timestamp,
                        "provider": provider,
                        "model": model,
                        "profile": profile,
                        "entry_price": entry,
                        "future": future,
                        **outcome,
                    }
                    if outcome["status"] == "OK":
                        decision = outcome["decision"]
                        final = risk.evaluate_order(
                            decision["action"],
                            decision["conviction"],
                            payload,
                            current_exposure=float(payload["portfolio_context"]["current_exposure_percentage"]),
                        )
                        row["risk"] = final
                        row["scores"] = {
                            horizon: _score(decision["action"], data["move_pct"])
                            for horizon, data in future.items()
                        }
                    rows.append(row)
    return {
        "generated_at": int(time.time()),
        "paper_only": True,
        "threshold_pct": 0.2,
        "fixed_plan": FIXED_PLAN.model_dump(),
        "models": args.models,
        "profiles": args.profiles,
        "results": rows,
    }


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# Tool-Augmented LLM Benchmark",
        "",
        "Paper-only historical benchmark. No portfolio mutation and no order execution.",
        "",
        "| Regime | Cycle | Model | Prompt | LLM | Risk | 15m | 60m |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for row in report["results"]:
        if row.get("status") != "OK":
            lines.append(
                f"| {row['scenario']} | {row['cycle']} | {row.get('model', '-')} | {row.get('profile', '-')} | {row.get('status')} | - | - | - |"
            )
            continue
        decision = row["decision"]
        lines.append(
            f"| {row['scenario']} | {row['cycle']} | {row['model']} | {row['profile']} | "
            f"{decision['action']} {decision['conviction']}% | {row['risk']['action']} | "
            f"{row['scores']['15']} ({row['future']['15']['move_pct']}%) | "
            f"{row['scores']['60']} ({row['future']['60']['move_pct']}%) |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description="Compare LLM models/prompts with deterministic historical tools.")
    parser.add_argument("--models", nargs="+", default=["groq:openai/gpt-oss-120b", "groq:qwen/qwen3.6-27b"])
    parser.add_argument("--profiles", nargs="+", choices=sorted(SYSTEM_PROMPT_PROFILES), default=["evidence_balanced", "trend_following"])
    parser.add_argument("--scenarios", nargs="+", choices=sorted(SCENARIOS), default=sorted(SCENARIOS))
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--asset", default="BTC/BRL")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--json-out", default=str(REPORTS_DIR / "last_tool_llm_benchmark.json"))
    parser.add_argument("--md-out", default=str(REPORTS_DIR / "last_tool_llm_benchmark.md"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.cycles <= 10:
        raise ValueError("cycles deve estar entre 1 e 10")
    report = run(args)
    json_path = Path(args.json_out)
    md_path = Path(args.md_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, md_path)
    print(f"JSON salvo em: {json_path.resolve()}")
    print(f"Markdown salvo em: {md_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
