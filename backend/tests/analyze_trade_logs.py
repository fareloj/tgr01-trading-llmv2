import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database
from backend.core.db_models import klines, trade_logs, virtual_portfolio


def print_table(title: str, rows: list[dict]):
    print(f"\n{title}")
    if not rows:
        print("  (empty)")
        return
    for row in rows:
        print(f"  {row}")


def classify_reason(reason: str, llm_reasoning: str = "") -> str:
    reason = reason or ""
    combined = f"{reason} {llm_reasoning or ''}".lower()
    if reason.startswith("Directional Gate"):
        return "directional_gate"
    if reason.startswith("Cooldown"):
        return "cooldown"
    if "stale" in reason.lower():
        return "stale_data"
    if any(term in combined for term in ("llm technical failure", "system api error", "validation failed")):
        return "llm_technical_failure"
    if reason.startswith("LLM sugeriu acao invalida"):
        return "invalid_llm_action"
    if reason.startswith("LLM sugeriu"):
        return "llm_hold"
    if reason.startswith("Aprovado"):
        return "approved"
    if "Confianca Hibrida" in reason or "Conviccao" in reason:
        return "confidence"
    return "other"


def load_snapshot(raw_snapshot: str | None) -> dict | None:
    if not raw_snapshot:
        return None
    try:
        return json.loads(raw_snapshot)
    except json.JSONDecodeError:
        return None


def _dicts(result) -> list[dict]:
    return [dict(row._mapping) for row in result]


def _scoped(stmt, since_id: int | None):
    return stmt.where(trade_logs.c.id >= since_id) if since_id is not None else stmt


def _snapshot_sections(rows: list[dict], limit: int) -> None:
    snapshots = []
    without_snapshot = 0
    for row in rows:
        snapshot = load_snapshot(row.get("payload_snapshot_json"))
        if snapshot is None:
            without_snapshot += 1
        else:
            snapshots.append((row["id"], snapshot))

    print_table("Cobertura de snapshots", [{"with_snapshot": len(snapshots), "without_snapshot": without_snapshot}])
    health_counts = {}
    term_counts = {}
    for _, snapshot in snapshots:
        health = snapshot.get("data_health", {})
        risk = snapshot.get("news_risk", {})
        key = (
            bool(health.get("is_market_data_stale")),
            bool(health.get("is_news_stale")),
            bool(risk.get("has_negative_red_flag")),
            risk.get("risk_level", "UNKNOWN"),
        )
        health_counts[key] = health_counts.get(key, 0) + 1
        for term in risk.get("matched_terms", []):
            term_counts[term] = term_counts.get(term, 0) + 1

    print_table(
        "Data health e news risk nos snapshots",
        [
            {
                "market_stale": key[0],
                "news_stale": key[1],
                "news_red_flag": key[2],
                "risk_level": key[3],
                "count": count,
            }
            for key, count in sorted(health_counts.items(), key=lambda item: item[1], reverse=True)
        ],
    )
    print_table(
        "Termos negativos nos snapshots",
        [{"term": term, "count": count} for term, count in sorted(term_counts.items(), key=lambda item: item[1], reverse=True)],
    )
    compact = []
    for log_id, snapshot in snapshots[: min(limit, 5)]:
        technical = snapshot.get("technical", {})
        health = snapshot.get("data_health", {})
        risk = snapshot.get("news_risk", {})
        portfolio = snapshot.get("portfolio", {})
        compact.append(
            {
                "id": log_id,
                "price": technical.get("current_price"),
                "rsi": f"{technical.get('rsi_value')} {technical.get('rsi_status')}",
                "macd": f"{technical.get('macd_histogram')} {technical.get('macd_status')}",
                "atr": technical.get("volatility_atr"),
                "kline_age": health.get("kline_age_seconds"),
                "news_age": health.get("news_age_seconds"),
                "news_risk": risk.get("risk_level"),
                "matched_terms": risk.get("matched_terms", []),
                "exposure_pct": portfolio.get("current_exposure_percentage"),
            }
        )
    print_table("Ultimos snapshots compactos", compact)


def analyze(limit: int, since_id: int | None = None):
    print(f"DB: {database.get_database_label()}")
    if since_id is not None:
        print(f"Filtro: trade_logs.id >= {since_id}")

    with database.engine.connect() as conn:
        actions = _dicts(
            conn.execute(
                _scoped(
                    select(trade_logs.c.action, func.count().label("count")).group_by(trade_logs.c.action),
                    since_id,
                ).order_by(func.count().desc())
            )
        )
        llm_vs_final = _dicts(
            conn.execute(
                _scoped(
                    select(trade_logs.c.llm_action, trade_logs.c.action, func.count().label("count")).group_by(
                        trade_logs.c.llm_action, trade_logs.c.action
                    ),
                    since_id,
                ).order_by(func.count().desc())
            )
        )
        reasons = _dicts(
            conn.execute(
                _scoped(
                    select(trade_logs.c.reasoning, func.count().label("count")).group_by(trade_logs.c.reasoning),
                    since_id,
                ).order_by(func.count().desc()).limit(limit)
            )
        )
        llm_reasons = _dicts(
            conn.execute(
                _scoped(
                    select(trade_logs.c.llm_reasoning, func.count().label("count")).group_by(trade_logs.c.llm_reasoning),
                    since_id,
                ).order_by(func.count().desc()).limit(limit)
            )
        )
        all_rows = _dicts(conn.execute(_scoped(select(trade_logs), since_id).order_by(trade_logs.c.id.desc())))
        approved_stmt = select(
            trade_logs.c.id,
            trade_logs.c.timestamp,
            trade_logs.c.llm_action,
            trade_logs.c.llm_reasoning,
            trade_logs.c.action,
            trade_logs.c.llm_conviction,
            trade_logs.c.executed_size,
            trade_logs.c.execution_price,
            trade_logs.c.reasoning,
        ).where(trade_logs.c.action.in_(["BUY", "SELL"]))
        if since_id is not None:
            approved_stmt = approved_stmt.where(trade_logs.c.id >= since_id)
        approved = _dicts(conn.execute(approved_stmt.order_by(trade_logs.c.id.desc()).limit(limit)))

        execution_stmt = select(
            trade_logs.c.id,
            trade_logs.c.action,
            trade_logs.c.executed_size,
            trade_logs.c.execution_price,
            trade_logs.c.effective_price,
            trade_logs.c.slippage_rate,
            trade_logs.c.fee_brl,
            trade_logs.c.gross_notional_brl,
            trade_logs.c.net_notional_brl,
            trade_logs.c.brl_delta,
            trade_logs.c.btc_delta,
            trade_logs.c.realized_pnl_brl,
            trade_logs.c.equity_before_brl,
            trade_logs.c.equity_after_brl,
        ).where(trade_logs.c.action.in_(["BUY", "SELL"]))
        if since_id is not None:
            execution_stmt = execution_stmt.where(trade_logs.c.id >= since_id)
        execution_rows = _dicts(conn.execute(execution_stmt.order_by(trade_logs.c.id.desc()).limit(limit)))
        for row in execution_rows:
            row["slippage_pct"] = round(float(row.get("slippage_rate") or 0.0) * 100.0, 4)
            row["immediate_equity_delta_brl"] = round(
                float(row.get("equity_after_brl") or 0.0) - float(row.get("equity_before_brl") or 0.0), 4
            )
        execution_summary_stmt = select(
            func.count().label("executed_orders"),
            func.coalesce(func.sum(trade_logs.c.fee_brl), 0.0).label("total_fees_brl"),
            func.coalesce(func.sum(trade_logs.c.realized_pnl_brl), 0.0).label("realized_pnl_brl"),
            func.coalesce(func.avg(trade_logs.c.slippage_rate) * 100.0, 0.0).label("avg_slippage_pct"),
        ).where(trade_logs.c.action.in_(["BUY", "SELL"]))
        if since_id is not None:
            execution_summary_stmt = execution_summary_stmt.where(trade_logs.c.id >= since_id)
        execution_summary = _dicts(conn.execute(execution_summary_stmt))
        portfolio = _dicts(
            conn.execute(select(virtual_portfolio.c.currency, virtual_portfolio.c.amount).order_by(virtual_portfolio.c.currency))
        )
        latest_price_row = conn.execute(
            select(klines.c.close)
            .where(klines.c.asset == "BTC/BRL", klines.c.timeframe == "1m")
            .order_by(klines.c.timestamp.desc())
            .limit(1)
        ).first()

    print_table("Acoes finais", actions)
    print_table("LLM vs Final", llm_vs_final)
    print_table("Motivos mais comuns", reasons)
    print_table("Justificativas LLM mais comuns", llm_reasons)
    briefs = [
        {key: row.get(key) for key in ("id", "llm_action", "action", "llm_reasoning", "llm_decision_brief")}
        for row in all_rows[: min(limit, 5)]
    ]
    print_table("Resumos humanos LLM recentes", briefs)
    buckets = {}
    for row in all_rows:
        bucket = classify_reason(row.get("reasoning"), row.get("llm_reasoning"))
        buckets[bucket] = buckets.get(bucket, 0) + 1
    print_table("Buckets de bloqueio/aprovacao", [{"bucket": key, "count": value} for key, value in sorted(buckets.items())])
    print_table("Ordens aprovadas", approved)
    print_table("Execucao paper: taxas, slippage e PnL", execution_rows)
    print_table("Resumo de execucao paper", execution_summary)
    recent_keys = (
        "id", "timestamp", "llm_action", "llm_reasoning", "llm_decision_brief", "action",
        "llm_conviction", "system_reliability", "final_confidence", "executed_size", "execution_price", "reasoning",
    )
    print_table("Ultimos logs", [{key: row.get(key) for key in recent_keys} for row in all_rows[:limit]])
    _snapshot_sections(all_rows, limit)
    print_table("Portfolio virtual", portfolio)
    if latest_price_row:
        latest_price = float(latest_price_row.close)
        amounts = {row["currency"]: float(row["amount"]) for row in portfolio}
        total_equity = amounts.get("BRL", 0.0) + amounts.get("BTC", 0.0) * latest_price
        exposure = amounts.get("BTC", 0.0) * latest_price / total_equity * 100.0 if total_equity else 0.0
        print("\nEquity paper")
        print(f"  latest_price: {latest_price:.2f}")
        print(f"  total_equity_brl: {total_equity:.2f}")
        print(f"  exposure_pct: {exposure:.2f}")


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze TGR-01 PostgreSQL paper-trading audit logs.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--since-id", type=int, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    analyze(args.limit, since_id=args.since_id)
