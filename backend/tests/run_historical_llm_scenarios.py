import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BACKEND_DIR / "reports"
sys.path.insert(0, str(BACKEND_DIR))

from agents.decision_agent import DecisionAgent, has_llm_api_key
from core.database import get_connection
from features.payload_builder import build_agent_payload
from risk.risk_manager import RiskManager

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


def parse_local_datetime(value: str) -> int:
    dt = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return int(dt.replace(tzinfo=LOCAL_TZ).timestamp())


def local_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def fetch_cycle_timestamps(
    *,
    asset: str,
    timeframe: str,
    from_ts: int,
    to_ts: int,
    cycles: int,
    step_seconds: int,
) -> list[int]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        rows = cursor.execute(
            """
            SELECT timestamp
            FROM klines
            WHERE asset = ? AND timeframe = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
            """,
            (asset, timeframe, from_ts, to_ts),
        ).fetchall()
    finally:
        conn.close()

    timestamps = [int(row["timestamp"]) for row in rows]
    if not timestamps:
        return []

    selected = []
    next_allowed = timestamps[0]
    for timestamp in timestamps:
        if timestamp < next_allowed:
            continue
        selected.append(timestamp)
        next_allowed = timestamp + step_seconds
        if len(selected) >= cycles:
            break
    return selected


def run_scenario(
    *,
    name: str,
    asset: str,
    timeframe: str,
    from_ts: int,
    to_ts: int,
    cycles: int,
    step_seconds: int,
) -> dict:
    timestamps = fetch_cycle_timestamps(
        asset=asset,
        timeframe=timeframe,
        from_ts=from_ts,
        to_ts=to_ts,
        cycles=cycles,
        step_seconds=step_seconds,
    )
    if not timestamps:
        return {
            "scenario": name,
            "status": "NO_KLINES",
            "from": from_ts,
            "to": to_ts,
            "results": [],
        }

    if not has_llm_api_key():
        raise RuntimeError("Nenhuma chave LLM configurada para teste historico.")

    agent = DecisionAgent()
    risk = RiskManager(max_exposure=80.0, cooldown_minutes=0)
    results = []

    for index, timestamp in enumerate(timestamps, start=1):
        payload = build_agent_payload(asset=asset, timeframe=timeframe, as_of_timestamp=timestamp)
        if payload.get("status") == "ERROR":
            results.append(
                {
                    "cycle": index,
                    "timestamp": timestamp,
                    "local_time": local_time(timestamp),
                    "status": "PAYLOAD_ERROR",
                    "error": payload,
                }
            )
            continue

        decision = agent.evaluate_market(payload)
        exposure = payload.get("portfolio_context", {}).get("current_exposure_percentage", 0.0)
        final_order = risk.evaluate_order(
            llm_action=decision.action,
            llm_conviction=decision.conviction,
            payload=payload,
            current_exposure=float(exposure or 0.0),
        )
        technical = payload.get("technical_context", {})
        data_health = payload.get("data_health", {})
        news_risk = payload.get("news_risk", {})
        results.append(
            {
                "cycle": index,
                "timestamp": timestamp,
                "local_time": local_time(timestamp),
                "price": technical.get("current_price"),
                "rsi": technical.get("rsi"),
                "macd": technical.get("macd"),
                "atr": technical.get("volatility_atr"),
                "news_risk": news_risk,
                "data_health": data_health,
                "llm": decision.model_dump(),
                "risk": final_order,
            }
        )

    return {
        "scenario": name,
        "status": "OK",
        "from": from_ts,
        "to": to_ts,
        "cycles_requested": cycles,
        "cycles_evaluated": len(results),
        "step_seconds": step_seconds,
        "results": results,
    }


def write_markdown(report: dict, output: Path) -> None:
    lines = [
        f"# Historical LLM Scenario: {report['scenario']}",
        "",
        f"Status: `{report['status']}`",
        f"Window: `{local_time(report['from'])}` to `{local_time(report['to'])}`",
        "",
    ]
    for item in report.get("results", []):
        if item.get("status") == "PAYLOAD_ERROR":
            lines.append(f"## Cycle {item['cycle']} / {item['local_time']}")
            lines.append("")
            lines.append("Payload error.")
            lines.append("")
            continue
        llm = item["llm"]
        risk = item["risk"]
        rsi = item["rsi"]
        macd = item["macd"]
        lines.append(f"## Cycle {item['cycle']} / {item['local_time']}")
        lines.append("")
        lines.append(f"- Price: `{item['price']}`")
        lines.append(f"- RSI: `{rsi.get('value')} {rsi.get('status')}`")
        lines.append(f"- MACD: `{macd.get('histogram')} {macd.get('status')}`")
        lines.append(f"- LLM: `{llm['action']}` `{llm['conviction']}%` - {llm['reasoning']}")
        lines.append(f"- Risk: `{risk['action']}` - {risk['reason']}")
        lines.append("")
        lines.append("```text")
        lines.append(llm.get("decision_brief") or "")
        lines.append("```")
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LLM/Risk checks on historical candle windows without executing orders.")
    parser.add_argument("--name", default="historical_window")
    parser.add_argument("--from-local", required=True, help='Start datetime, format "YYYY-MM-DD HH:MM".')
    parser.add_argument("--to-local", required=True, help='End datetime, format "YYYY-MM-DD HH:MM".')
    parser.add_argument("--asset", default="BTC/BRL")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--step-seconds", type=int, default=60)
    parser.add_argument("--json-out", default=str(REPORTS_DIR / "last_historical_llm_scenario.json"))
    parser.add_argument("--md-out", default=str(REPORTS_DIR / "last_historical_llm_scenario.md"))
    args = parser.parse_args()

    REPORTS_DIR.mkdir(exist_ok=True)
    report = run_scenario(
        name=args.name,
        asset=args.asset,
        timeframe=args.timeframe,
        from_ts=parse_local_datetime(args.from_local),
        to_ts=parse_local_datetime(args.to_local),
        cycles=args.cycles,
        step_seconds=args.step_seconds,
    )
    json_path = Path(args.json_out)
    md_path = Path(args.md_out)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, md_path)

    print(f"JSON salvo em: {json_path.resolve()}")
    print(f"Markdown salvo em: {md_path.resolve()}")
    for item in report.get("results", []):
        if "llm" not in item:
            print(f"[{item['cycle']}] {item['local_time']} PAYLOAD_ERROR")
            continue
        print(
            f"[{item['cycle']}] {item['local_time']} price={item['price']} "
            f"RSI={item['rsi'].get('status')} MACD={item['macd'].get('status')} "
            f"LLM={item['llm']['action']} {item['llm']['conviction']}% "
            f"RISK={item['risk']['action']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
