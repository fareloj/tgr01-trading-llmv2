import argparse
import json
import sqlite3
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "trading_v2.db"


def fetch_rows(cursor, query: str, params: tuple = ()):
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def print_table(title: str, rows: list[dict]):
    print(f"\n{title}")
    if not rows:
        print("  (empty)")
        return
    for row in rows:
        print(f"  {row}")


def classify_reason(reason: str, llm_reasoning: str = "") -> str:
    reason = reason or ""
    llm_reasoning = llm_reasoning or ""
    combined = f"{reason} {llm_reasoning}".lower()
    if reason.startswith("Directional Gate"):
        return "directional_gate"
    if reason.startswith("Cooldown"):
        return "cooldown"
    if "stale" in reason.lower():
        return "stale_data"
    if "llm technical failure" in combined or "system api error" in combined or "validation failed" in combined:
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


def scoped_where(since_id: int | None) -> tuple[str, tuple]:
    if since_id is None:
        return "", ()
    return "WHERE id >= ?", (since_id,)


def has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return column in {row["name"] for row in cursor.fetchall()}


def has_columns(cursor, table: str, columns: set[str]) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row["name"] for row in cursor.fetchall()}
    return columns.issubset(existing)


def load_snapshot(raw_snapshot: str | None) -> dict | None:
    if not raw_snapshot:
        return None
    try:
        return json.loads(raw_snapshot)
    except json.JSONDecodeError:
        return None


def print_snapshot_summary(cursor, where_clause: str, where_params: tuple, limit: int):
    if not has_column(cursor, "trade_logs", "payload_snapshot_json"):
        print("\nSnapshots de payload")
        print("  (coluna ausente; rode init_db para aplicar a migracao)")
        return

    cursor.execute(
        f"""
        SELECT id, payload_snapshot_json
        FROM trade_logs
        {where_clause}
        ORDER BY id DESC
        """,
        where_params,
    )
    rows = cursor.fetchall()
    snapshots = []
    without_snapshot = 0
    for row in rows:
        snapshot = load_snapshot(row["payload_snapshot_json"])
        if snapshot is None:
            without_snapshot += 1
            continue
        snapshots.append((row["id"], snapshot))

    print_table(
        "Cobertura de snapshots",
        [
            {
                "with_snapshot": len(snapshots),
                "without_snapshot": without_snapshot,
            }
        ],
    )

    health_counts = {}
    term_counts = {}
    for _, snapshot in snapshots:
        health = snapshot.get("data_health", {})
        news_risk = snapshot.get("news_risk", {})
        key = (
            bool(health.get("is_market_data_stale")),
            bool(health.get("is_news_stale")),
            bool(news_risk.get("has_negative_red_flag")),
            news_risk.get("risk_level", "UNKNOWN"),
        )
        health_counts[key] = health_counts.get(key, 0) + 1
        for term in news_risk.get("matched_terms", []):
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

    compact_rows = []
    for log_id, snapshot in snapshots[: min(limit, 5)]:
        technical = snapshot.get("technical", {})
        health = snapshot.get("data_health", {})
        news_risk = snapshot.get("news_risk", {})
        portfolio = snapshot.get("portfolio", {})
        compact_rows.append(
            {
                "id": log_id,
                "price": technical.get("current_price"),
                "rsi": f"{technical.get('rsi_value')} {technical.get('rsi_status')}",
                "macd": f"{technical.get('macd_histogram')} {technical.get('macd_status')}",
                "atr": technical.get("volatility_atr"),
                "kline_age": health.get("kline_age_seconds"),
                "news_age": health.get("news_age_seconds"),
                "news_risk": news_risk.get("risk_level"),
                "matched_terms": news_risk.get("matched_terms", []),
                "exposure_pct": portfolio.get("current_exposure_percentage"),
            }
        )
    print_table("Ultimos snapshots compactos", compact_rows)


def analyze(db_path: Path, limit: int, since_id: int | None = None):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print(f"DB: {db_path.resolve()}")
    if since_id is not None:
        print(f"Filtro: trade_logs.id >= {since_id}")

    where_clause, where_params = scoped_where(since_id)

    print_table(
        "Acoes finais",
        fetch_rows(
            cursor,
            f"""
            SELECT action, COUNT(*) AS count
            FROM trade_logs
            {where_clause}
            GROUP BY action
            ORDER BY count DESC
            """,
            where_params,
        ),
    )

    print_table(
        "LLM vs Final",
        fetch_rows(
            cursor,
            f"""
            SELECT llm_action, action, COUNT(*) AS count
            FROM trade_logs
            {where_clause}
            GROUP BY llm_action, action
            ORDER BY count DESC
            """,
            where_params,
        ),
    )

    reasons = fetch_rows(
        cursor,
        f"""
        SELECT reasoning, COUNT(*) AS count
        FROM trade_logs
        {where_clause}
        GROUP BY reasoning
        ORDER BY count DESC
        LIMIT ?
        """,
        where_params + (limit,),
    )
    print_table("Motivos mais comuns", reasons)

    llm_reasons = fetch_rows(
        cursor,
        f"""
        SELECT llm_reasoning, COUNT(*) AS count
        FROM trade_logs
        {where_clause}
        GROUP BY llm_reasoning
        ORDER BY count DESC
        LIMIT ?
        """,
        where_params + (limit,),
    )
    print_table("Justificativas LLM mais comuns", llm_reasons)

    if has_column(cursor, "trade_logs", "llm_decision_brief"):
        print_table(
            "Resumos humanos LLM recentes",
            fetch_rows(
                cursor,
                f"""
                SELECT id, llm_action, action, llm_reasoning, llm_decision_brief
                FROM trade_logs
                {where_clause}
                ORDER BY id DESC
                LIMIT ?
                """,
                where_params + (min(limit, 5),),
            ),
        )

    cursor.execute(f"SELECT reasoning, llm_reasoning FROM trade_logs {where_clause}", where_params)
    buckets = {}
    for row in cursor.fetchall():
        bucket = classify_reason(row["reasoning"], row["llm_reasoning"])
        buckets[bucket] = buckets.get(bucket, 0) + 1
    print_table("Buckets de bloqueio/aprovacao", [{"bucket": key, "count": value} for key, value in sorted(buckets.items())])

    print_table(
        "Ordens aprovadas",
        fetch_rows(
            cursor,
            f"""
            SELECT id, timestamp, llm_action, llm_reasoning, action, llm_conviction,
                   executed_size, execution_price, reasoning
            FROM trade_logs
            WHERE action IN ('BUY', 'SELL')
            {"AND id >= ?" if since_id is not None else ""}
            ORDER BY id DESC
            LIMIT ?
            """,
            ((since_id,) if since_id is not None else ()) + (limit,),
        ),
    )

    if has_columns(
        cursor,
        "trade_logs",
        {
            "fee_brl",
            "slippage_rate",
            "effective_price",
            "equity_before_brl",
            "equity_after_brl",
            "realized_pnl_brl",
        },
    ):
        print_table(
            "Execucao paper: taxas, slippage e PnL",
            fetch_rows(
                cursor,
                f"""
                SELECT id, action, executed_size, execution_price, effective_price,
                       ROUND(slippage_rate * 100.0, 4) AS slippage_pct,
                       fee_brl, gross_notional_brl, net_notional_brl,
                       brl_delta, btc_delta, realized_pnl_brl,
                       equity_before_brl, equity_after_brl,
                       ROUND(equity_after_brl - equity_before_brl, 4) AS immediate_equity_delta_brl
                FROM trade_logs
                WHERE action IN ('BUY', 'SELL')
                {"AND id >= ?" if since_id is not None else ""}
                ORDER BY id DESC
                LIMIT ?
                """,
                ((since_id,) if since_id is not None else ()) + (limit,),
            ),
        )

        execution_summary = fetch_rows(
            cursor,
            f"""
            SELECT
                COUNT(*) AS executed_orders,
                COALESCE(SUM(fee_brl), 0.0) AS total_fees_brl,
                COALESCE(SUM(realized_pnl_brl), 0.0) AS realized_pnl_brl,
                COALESCE(AVG(slippage_rate) * 100.0, 0.0) AS avg_slippage_pct
            FROM trade_logs
            WHERE action IN ('BUY', 'SELL')
            {"AND id >= ?" if since_id is not None else ""}
            """,
            ((since_id,) if since_id is not None else ()),
        )
        print_table("Resumo de execucao paper", execution_summary)

    print_table(
        "Ultimos logs",
        fetch_rows(
            cursor,
            f"""
            SELECT id, timestamp, llm_action, llm_reasoning,
                   {"llm_decision_brief," if has_column(cursor, "trade_logs", "llm_decision_brief") else ""}
                   action, llm_conviction,
                   system_reliability, final_confidence, executed_size,
                   execution_price, reasoning
            FROM trade_logs
            {where_clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            where_params + (limit,),
        ),
    )

    print_snapshot_summary(cursor, where_clause, where_params, limit)

    portfolio = fetch_rows(cursor, "SELECT currency, amount FROM virtual_portfolio ORDER BY currency")
    print_table("Portfolio virtual", portfolio)

    cursor.execute("SELECT close FROM klines WHERE asset='BTC/BRL' AND timeframe='1m' ORDER BY timestamp DESC LIMIT 1")
    latest_price_row = cursor.fetchone()
    if latest_price_row:
        latest_price = float(latest_price_row["close"])
        amounts = {row["currency"]: float(row["amount"]) for row in portfolio}
        total_equity = amounts.get("BRL", 0.0) + amounts.get("BTC", 0.0) * latest_price
        exposure = (amounts.get("BTC", 0.0) * latest_price / total_equity * 100.0) if total_equity > 0 else 0.0
        print("\nEquity paper")
        print(f"  latest_price: {latest_price:.2f}")
        print(f"  total_equity_brl: {total_equity:.2f}")
        print(f"  exposure_pct: {exposure:.2f}")

    conn.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze TGR-01 paper trading audit logs.")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite DB path. Default: backend/trading_v2.db")
    parser.add_argument("--limit", type=int, default=10, help="Rows per detailed section. Default: 10")
    parser.add_argument("--since-id", type=int, default=None, help="Only analyze trade_logs with id >= this value.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    analyze(Path(args.db), args.limit, since_id=args.since_id)
