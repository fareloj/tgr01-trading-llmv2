import argparse
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

    print_table(
        "Ultimos logs",
        fetch_rows(
            cursor,
            f"""
            SELECT id, timestamp, llm_action, llm_reasoning, action, llm_conviction,
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
