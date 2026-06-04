import argparse
import email.utils
import hashlib
import random
import sys
import time
import defusedxml.ElementTree as ET
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from core.database import get_connection, get_db_path, init_db

DEFAULT_RSS_FEEDS = {
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
}

MOCK_HEADLINES = [
    "SEC indica que pode aprovar novo regulamento na proxima semana.",
    "Inflacao nos EUA sobe acima do esperado. Mercados recuam.",
    "Baleia transfere 10.000 BTC para exchange desconhecida.",
    "Corretoras globais anunciam reducao nas taxas de saque.",
    "Bolsas abrem em alta com dados de emprego mais fortes que o previsto.",
    "Rumores sobre proibicao de criptomoedas na Asia causam tensao.",
    "Analistas projetam alvo de R$ 400.000 para o Bitcoin ate dezembro.",
]

MOCK_SOURCES = ["CoinDesk", "Bloomberg", "Exame", "InfoMoney", "CryptoPanic"]


def headline_hash(headline: str, source: str) -> str:
    return hashlib.md5(f"{source}|{headline}".encode("utf-8")).hexdigest()


def parse_rss_timestamp(value: str | None) -> int:
    if not value:
        return int(time.time())
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return int(parsed.timestamp())
    except Exception:
        return int(time.time())


def parse_rss_items(xml_text: str, source: str, limit: int) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []

    for item in root.findall(".//item"):
        title_node = item.find("title")
        if title_node is None or not title_node.text:
            continue

        published_node = item.find("pubDate")
        if published_node is None:
            published_node = item.find("{http://purl.org/dc/elements/1.1/}date")

        headline = " ".join(title_node.text.split())
        items.append(
            {
                "timestamp": parse_rss_timestamp(published_node.text if published_node is not None else None),
                "headline": headline,
                "source": source,
            }
        )

        if len(items) >= limit:
            break

    return items


def fetch_real_news(feed_limit: int = 10) -> list[dict]:
    news = []
    headers = {"User-Agent": "TGR-01-Trading-LLM-V2/0.1"}

    for source, url in DEFAULT_RSS_FEEDS.items():
        try:
            response = requests.get(url, timeout=10, headers=headers)
            response.raise_for_status()
            parsed_items = parse_rss_items(response.text, source=source, limit=feed_limit)
            news.extend(parsed_items)
            print(f"[News Worker] RSS {source}: {len(parsed_items)} itens lidos.")
        except Exception as e:
            print(f"[News Worker] RSS {source} falhou: {type(e).__name__}: {e}")

    return news


def fetch_mock_news() -> list[dict]:
    headline = random.choice(MOCK_HEADLINES)
    source = random.choice(MOCK_SOURCES)
    return [{"timestamp": int(time.time()), "headline": headline, "source": source}]


def persist_news(news_items: list[dict]) -> int:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        inserted = 0

        for item in news_items:
            source = item["source"]
            headline = item["headline"]
            cursor.execute(
                """
                INSERT OR IGNORE INTO news (timestamp, headline, headline_hash, source)
                VALUES (?, ?, ?, ?)
                """,
                (int(item["timestamp"]), headline, headline_hash(headline, source), source),
            )
            if cursor.rowcount > 0:
                inserted += 1
                print(f"[News] Inserida: {source} | {headline[:90]}")

        cursor.execute(
            """
            INSERT INTO system_health (worker_name, last_heartbeat)
            VALUES ('news_worker', ?)
            ON CONFLICT(worker_name)
            DO UPDATE SET last_heartbeat=excluded.last_heartbeat
            """,
            (int(time.time()),),
        )

        conn.commit()
        return inserted
    finally:
        conn.close()


def run_news_worker(mode: str = "mock", interval: int = 900, once: bool = False, feed_limit: int = 10):
    print(f"Iniciando News Worker em modo {mode}...")
    init_db()
    print(f"[News Worker] DB path: {get_db_path()}")
    print(f"[News Worker] interval={interval}s once={once}")

    while True:
        try:
            if mode == "real":
                news_items = fetch_real_news(feed_limit=feed_limit)
            else:
                news_items = fetch_mock_news()

            inserted = persist_news(news_items)
            print(f"[News Worker] Ciclo concluido. Itens recebidos={len(news_items)} novos={inserted}")

            if once:
                return

            time.sleep(interval)

        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [News Worker] Erro critico: {type(e).__name__}: {e}")
            if once:
                raise
            time.sleep(min(interval, 60))


def parse_args():
    parser = argparse.ArgumentParser(description="Populate SQLite with mock or real RSS crypto news.")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock", help="News source mode. Default: mock")
    parser.add_argument("--interval", type=int, default=900, help="Seconds between cycles. Default: 900")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--feed-limit", type=int, default=10, help="Max RSS items per source per cycle. Default: 10")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_news_worker(mode=args.mode, interval=args.interval, once=args.once, feed_limit=args.feed_limit)
