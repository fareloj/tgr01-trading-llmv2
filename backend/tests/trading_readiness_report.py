import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "trading_v2.db"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.decision_agent import load_api_keys
from core.database import get_connection, get_db_path, init_db
from features.payload_builder import build_agent_payload
from analyze_trade_logs import classify_reason


def local_dt(timestamp: int | None) -> str:
    if timestamp is None:
        return "None"
    return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")


def print_section(title: str):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def fetch_one(query: str, params: tuple = ()):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()
    finally:
        conn.close()


def fetch_all(query: str, params: tuple = ()):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        conn.close()


def current_data_report(blockers: list[str], warnings: list[str]):
    print_section("Dados Atuais")
    now = int(time.time())
    print(f"DB: {get_db_path()}")
    print(f"Agora local: {local_dt(now)}")

    latest_kline = fetch_one(
        """
        SELECT timestamp, close
        FROM klines
        WHERE asset='BTC/BRL' AND timeframe='1m'
        ORDER BY timestamp DESC
        LIMIT 1
        """
    )
    if latest_kline is None:
        blockers.append("Sem candle BTC/BRL 1m no SQLite.")
        print("[FAIL] Sem candle BTC/BRL 1m.")
    else:
        age = now - int(latest_kline["timestamp"])
        print(f"Kline: {local_dt(latest_kline['timestamp'])} age={age}s close={latest_kline['close']}")
        if age > 300:
            blockers.append(f"Candle stale: {age}s > 300s.")
        elif age > 120:
            warnings.append(f"Candle fresco, mas acima de 120s: {age}s.")

    latest_news = fetch_one(
        """
        SELECT timestamp, source, headline
        FROM news
        ORDER BY timestamp DESC
        LIMIT 1
        """
    )
    if latest_news is None:
        warnings.append("Sem noticias no SQLite.")
        print("[WARN] Sem noticias.")
    else:
        age = now - int(latest_news["timestamp"])
        print(f"News: {local_dt(latest_news['timestamp'])} age={age}s source={latest_news['source']}")
        print(f"      {latest_news['headline'][:100]}")
        if age > 21600:
            warnings.append(f"Noticias stale: {age}s > 21600s.")


def worker_report(blockers: list[str], warnings: list[str]):
    print_section("Workers")
    now = int(time.time())
    workers = fetch_all("SELECT worker_name, last_heartbeat FROM system_health ORDER BY worker_name")
    worker_map = {row["worker_name"]: int(row["last_heartbeat"]) for row in workers}
    expected = {"price_worker": 300, "news_worker": 3600}

    for worker, max_age in expected.items():
        heartbeat = worker_map.get(worker)
        if heartbeat is None:
            blockers.append(f"{worker} sem heartbeat.")
            print(f"[FAIL] {worker}: sem heartbeat")
            continue
        age = now - heartbeat
        print(f"{worker}: heartbeat={local_dt(heartbeat)} age={age}s")
        if age > max_age:
            blockers.append(f"{worker} stale: {age}s > {max_age}s.")
        elif age > max_age / 2:
            warnings.append(f"{worker} heartbeat acima de metade do limite: {age}s.")


def payload_report(blockers: list[str], warnings: list[str]):
    print_section("Payload / Sinais")
    payload = build_agent_payload()
    if payload.get("status") == "ERROR":
        blockers.append(payload.get("message", "Payload retornou ERROR."))
        print(payload)
        return None

    tech = payload["technical_context"]
    health = payload.get("data_health", {})
    news_risk = payload.get("news_risk", {})
    portfolio = payload.get("portfolio_context", {})

    print(f"Preco: {tech.get('current_price')}")
    print(f"RSI: {tech.get('rsi')}")
    print(f"MACD: {tech.get('macd')}")
    print(f"ATR: {tech.get('volatility_atr')}")
    print(f"Data health: market_stale={health.get('is_market_data_stale')} news_stale={health.get('is_news_stale')}")
    print(f"News risk: {news_risk}")
    print(f"Portfolio: {portfolio}")

    if health.get("is_market_data_stale"):
        blockers.append("Payload marcou market_data_stale=True.")
    if health.get("is_news_stale"):
        warnings.append("Payload marcou news_stale=True.")
    if news_risk.get("has_negative_red_flag"):
        warnings.append(f"News red flag: {news_risk.get('matched_terms')}")

    return payload


def llm_report(warnings: list[str]):
    print_section("LLM")
    keys = load_api_keys()
    print(f"Chaves configuradas: {len(keys)}")
    if not keys:
        warnings.append("Nenhuma chave LLM configurada; pipeline usara mock/fallback.")


def audit_report(since_id: int | None, warnings: list[str]):
    print_section("Auditoria / Paper Trading")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        where = "WHERE id >= ?" if since_id is not None else ""
        params = (since_id,) if since_id is not None else ()

        cursor.execute(f"SELECT COUNT(*) AS count FROM trade_logs {where}", params)
        total = cursor.fetchone()["count"]
        print(f"Logs analisados: {total}" + (f" desde id {since_id}" if since_id is not None else ""))

        cursor.execute(f"SELECT action, COUNT(*) AS count FROM trade_logs {where} GROUP BY action ORDER BY count DESC", params)
        for row in cursor.fetchall():
            print(f"Action {row['action']}: {row['count']}")

        cursor.execute(f"SELECT reasoning, llm_reasoning FROM trade_logs {where}", params)
        buckets = {}
        for row in cursor.fetchall():
            bucket = classify_reason(row["reasoning"], row["llm_reasoning"])
            buckets[bucket] = buckets.get(bucket, 0) + 1
        print(f"Buckets: {dict(sorted(buckets.items()))}")

        if buckets.get("llm_technical_failure", 0) > 0:
            warnings.append(f"Falhas tecnicas LLM no periodo: {buckets['llm_technical_failure']}.")
        if buckets.get("stale_data", 0) > 0:
            warnings.append(f"Stale data auditado no periodo: {buckets['stale_data']}.")

        cursor.execute(
            """
            SELECT id, timestamp, llm_action, llm_reasoning, action, llm_conviction, reasoning
            FROM trade_logs
            ORDER BY id DESC
            LIMIT 5
            """
        )
        print("Ultimos 5 logs:")
        for row in cursor.fetchall():
            print(dict(row))

        cursor.execute("SELECT currency, amount FROM virtual_portfolio ORDER BY currency")
        portfolio_rows = cursor.fetchall()
        amounts = {row["currency"]: float(row["amount"]) for row in portfolio_rows}
        cursor.execute("SELECT close FROM klines WHERE asset='BTC/BRL' AND timeframe='1m' ORDER BY timestamp DESC LIMIT 1")
        price_row = cursor.fetchone()
        if price_row:
            latest_price = float(price_row["close"])
            total_equity = amounts.get("BRL", 0.0) + amounts.get("BTC", 0.0) * latest_price
            exposure = (amounts.get("BTC", 0.0) * latest_price / total_equity * 100.0) if total_equity else 0.0
            print(f"Equity: {total_equity:.2f} BRL | exposure={exposure:.2f}% | latest_price={latest_price:.2f}")
    finally:
        conn.close()


def final_verdict(blockers: list[str], warnings: list[str], strict: bool):
    print_section("Veredito")
    if blockers:
        print("BLOCKED")
        for item in blockers:
            print(f"[BLOCKER] {item}")
        return 1

    if warnings:
        print("PASS_WITH_WARNINGS")
        for item in warnings:
            print(f"[WARN] {item}")
        return 1 if strict else 0

    print("PASS")
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Generate an operational trading readiness report.")
    parser.add_argument("--since-id", type=int, default=None, help="Only evaluate trade logs with id >= this value.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when warnings exist.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    init_db()
    blockers: list[str] = []
    warnings: list[str] = []

    current_data_report(blockers, warnings)
    worker_report(blockers, warnings)
    payload_report(blockers, warnings)
    llm_report(warnings)
    audit_report(args.since_id, warnings)
    raise SystemExit(final_verdict(blockers, warnings, strict=args.strict))
