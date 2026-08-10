import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR.parent))

from backend.core.database import get_connection, get_db_path, init_db, print_db_diagnostics
from backend.core.clock_sync import check_clock_skew
from backend.core.db_models import klines, news, system_health
from backend.core.market_policy import MARKET_DATA_MAX_AGE_SECONDS
from backend.core.runtime_safety import MAX_FUTURE_MARKET_DATA_SECONDS, assess_worker_heartbeats


def local_date(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d")


def local_datetime(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")


def fetch_latest_kline(asset: str, timeframe: str):
    stmt = (
        select(klines.c.asset, klines.c.timeframe, klines.c.timestamp, klines.c.close)
        .where(klines.c.asset == asset, klines.c.timeframe == timeframe)
        .order_by(klines.c.timestamp.desc())
        .limit(1)
    )
    with get_connection() as conn:
        row = conn.execute(stmt).mappings().first()
        return dict(row) if row else None


def fetch_latest_news():
    stmt = select(news.c.timestamp, news.c.headline, news.c.source).order_by(news.c.timestamp.desc()).limit(1)
    with get_connection() as conn:
        row = conn.execute(stmt).mappings().first()
        return dict(row) if row else None


def fetch_worker_health():
    stmt = select(system_health.c.worker_name, system_health.c.last_heartbeat).order_by(system_health.c.worker_name)
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def fail(message: str):
    print(f"[FAIL] {message}")
    return 1


def warn(message: str):
    print(f"[WARN] {message}")


def ok(message: str):
    print(f"[OK] {message}")


def run_preflight(
    asset: str,
    timeframe: str,
    require_news_today: bool,
    max_kline_age_seconds: int,
    require_workers: bool,
    require_clock_sync: bool,
    max_clock_skew_seconds: int,
):
    init_db()
    print_db_diagnostics()

    now = int(time.time())
    today = local_date(now)
    print(f"[DATE] Hoje local: {today}")
    print(f"[DB] Path: {get_db_path()}")

    clock = check_clock_skew(max_skew_seconds=max_clock_skew_seconds)
    if clock["status"] == "UNAVAILABLE":
        message = "Nao foi possivel validar clock skew via HTTP."
        if require_clock_sync:
            return fail(message)
        warn(message)
    else:
        print(
            "[CLOCK] "
            f"skew={clock['skew_seconds']}s "
            f"tolerance={clock['max_skew_seconds']}s "
            f"status={clock['status']}"
        )
        if not clock["is_within_tolerance"]:
            return fail(
                f"Clock skew detectado: {clock['skew_seconds']}s. "
                "Sincronize o relogio do Windows antes de rodar pipeline."
            )
        ok("Relogio local validado por fontes HTTP.")

    latest_kline = fetch_latest_kline(asset, timeframe)
    if latest_kline is None:
        return fail(f"Nenhum candle encontrado para {asset} {timeframe}.")

    kline_timestamp = int(latest_kline["timestamp"])
    kline_date = local_date(kline_timestamp)
    kline_age = now - kline_timestamp
    print(
        "[KLINE] "
        f"{asset} {timeframe} ts={kline_timestamp} "
        f"local={local_datetime(kline_timestamp)} "
        f"age={kline_age}s close={latest_kline['close']}"
    )

    if kline_date != today:
        return fail(f"Ultimo candle nao e de hoje: {kline_date} != {today}. Reinicie price_worker ou rode seed.")

    if kline_age > max_kline_age_seconds:
        return fail(f"Ultimo candle esta stale: {kline_age}s > {max_kline_age_seconds}s.")
    if kline_age < -MAX_FUTURE_MARKET_DATA_SECONDS:
        return fail(f"Ultimo candle esta {-kline_age}s no futuro; clock ou fonte invalida.")

    ok("Candle mais recente bate com o dia atual e esta fresco.")

    latest_news = fetch_latest_news()
    if latest_news is None:
        if require_news_today:
            return fail("Nenhuma noticia encontrada no PostgreSQL.")
        warn("Nenhuma noticia encontrada no PostgreSQL.")
    else:
        news_timestamp = int(latest_news["timestamp"])
        news_date = local_date(news_timestamp)
        news_age = now - news_timestamp
        print(
            "[NEWS] "
            f"{latest_news['source']} local={local_datetime(news_timestamp)} "
            f"age={news_age}s headline={latest_news['headline'][:90]}"
        )
        if news_date != today:
            message = f"Ultima noticia nao e de hoje: {news_date} != {today}."
            if require_news_today:
                return fail(message)
            warn(message)
        else:
            ok("Noticia mais recente bate com o dia atual.")

    if require_workers:
        worker_rows = fetch_worker_health()
        assessment = assess_worker_heartbeats(worker_rows, now=now)
        for worker_name, age in assessment.ages_seconds.items():
            print(f"[WORKER] {worker_name} heartbeat_age={age}s")
        if not assessment.healthy:
            return fail("; ".join(assessment.failures))
        ok("Workers obrigatorios estao vivos.")

    ok("Preflight de data aprovado.")
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Check whether PostgreSQL market/news data matches today's local date.")
    parser.add_argument("--asset", default="BTC/BRL")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--require-news-today", action="store_true")
    parser.add_argument("--require-workers", action="store_true")
    parser.add_argument(
        "--max-kline-age-seconds",
        type=int,
        default=MARKET_DATA_MAX_AGE_SECONDS,
    )
    parser.add_argument("--require-clock-sync", action="store_true")
    parser.add_argument("--max-clock-skew-seconds", type=int, default=300)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(
        run_preflight(
            asset=args.asset,
            timeframe=args.timeframe,
            require_news_today=args.require_news_today,
            max_kline_age_seconds=args.max_kline_age_seconds,
            require_workers=args.require_workers,
            require_clock_sync=args.require_clock_sync,
            max_clock_skew_seconds=args.max_clock_skew_seconds,
        )
    )
