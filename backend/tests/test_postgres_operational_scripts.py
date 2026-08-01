import sys
import time

from sqlalchemy import func, select

from backend.core import database, repository
from backend.core.db_models import klines, news
from backend.tests import ingest_rag_sources, preflight_data_date, run_historical_llm_scenarios
from backend.tests import seed_historical_data, start_workers
from backend.tests import migrate_sqlite_to_postgres


def _kline(timestamp: int, close: float = 100000.0) -> dict:
    return {
        "asset": "BTC/BRL",
        "timeframe": "1m",
        "timestamp": timestamp,
        "open": close - 10,
        "high": close + 20,
        "low": close - 20,
        "close": close,
        "volume": 1.0,
    }


def _news(timestamp: int, suffix: str = "1") -> dict:
    return {
        "timestamp": timestamp,
        "headline": f"Noticia operacional {suffix}",
        "headline_hash": f"operational-{suffix}",
        "source": "pytest",
    }


def test_preflight_helpers_and_full_run_use_postgresql(monkeypatch):
    now = int(time.time())
    repository.add_klines([_kline(now - 30)])
    repository.add_news(_news(now - 20))
    repository.update_system_health("price_worker", now - 5)
    repository.update_system_health("news_worker", now - 5)
    monkeypatch.setattr(
        preflight_data_date,
        "check_clock_skew",
        lambda **_: {
            "status": "OK",
            "skew_seconds": 0,
            "max_skew_seconds": 300,
            "is_within_tolerance": True,
        },
    )

    assert preflight_data_date.fetch_latest_kline("BTC/BRL", "1m")["close"] == 100000.0
    assert preflight_data_date.fetch_latest_news()["source"] == "pytest"
    assert {row["worker_name"] for row in preflight_data_date.fetch_worker_health()} == {
        "price_worker",
        "news_worker",
    }
    assert preflight_data_date.run_preflight(
        asset="BTC/BRL",
        timeframe="1m",
        require_news_today=True,
        max_kline_age_seconds=300,
        require_workers=True,
        require_clock_sync=True,
        max_clock_skew_seconds=300,
    ) == 0


def test_historical_timestamp_selection_uses_step_spacing():
    repository.add_klines([_kline(1000 + offset, 100000 + offset) for offset in (0, 60, 120, 180, 240)])

    selected = run_historical_llm_scenarios.fetch_cycle_timestamps(
        asset="BTC/BRL",
        timeframe="1m",
        from_ts=1000,
        to_ts=1240,
        cycles=3,
        step_seconds=120,
    )

    assert selected == [1000, 1120, 1240]


def test_recent_news_ingest_respects_cutoff_and_limit(monkeypatch):
    now = int(time.time())
    repository.add_news(_news(now - 60, "fresh"))
    repository.add_news(_news(now - 7200, "old"))
    captured = []
    monkeypatch.setattr(ingest_rag_sources, "upsert_document", lambda **kwargs: captured.append(kwargs) or 77)

    ids = ingest_rag_sources.ingest_recent_news(hours=1, limit=10, dry_run=False)

    assert ids == [77]
    assert captured[0]["title"] == "Noticia operacional fresh"


def test_historical_seed_upserts_without_duplicate_rows(monkeypatch, capsys):
    payload = {
        "t": [1000, 1060],
        "o": [10, 11],
        "h": [12, 13],
        "l": [9, 10],
        "c": [11, 12],
        "v": [1, 2],
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(seed_historical_data.requests, "get", lambda *args, **kwargs: Response())
    seed_historical_data.seed_history(from_ts=1000, to_ts=1060)
    seed_historical_data.seed_history(from_ts=1000, to_ts=1060)

    with database.engine.connect() as connection:
        count = int(connection.scalar(select(func.count()).select_from(klines)) or 0)
    assert count == 2
    assert "2 candles historicos processados" in capsys.readouterr().out


def test_worker_launcher_uses_current_python_and_detects_early_exit(monkeypatch, tmp_path):
    commands = []

    class Process:
        pid = 123

        def __init__(self, command, **kwargs):
            commands.append(command)

        def poll(self):
            return None

    monkeypatch.setattr(start_workers, "LOG_DIR", tmp_path)
    monkeypatch.setattr(start_workers.subprocess, "Popen", Process)
    monkeypatch.setattr(start_workers.time, "sleep", lambda _: None)

    assert start_workers._start_worker(tmp_path / "worker.py", ["--once"], "worker") is True
    assert commands == [[sys.executable, "-u", str(tmp_path / "worker.py"), "--once"]]

    Process.poll = lambda self: 1
    assert start_workers._start_worker(tmp_path / "worker.py", [], "worker") is False


def test_legacy_sqlite_migration_is_read_only_and_validates_counts(tmp_path):
    source = tmp_path / "legacy.db"
    with migrate_sqlite_to_postgres.sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE klines (id INTEGER PRIMARY KEY, asset TEXT, timeframe TEXT, timestamp INTEGER, "
            "open REAL, high REAL, low REAL, close REAL, volume REAL)"
        )
        connection.execute(
            "INSERT INTO klines VALUES (9, 'BTC/BRL', '1m', 1000, 10, 12, 9, 11, 1)"
        )
        connection.execute(
            "CREATE TABLE virtual_portfolio (id INTEGER PRIMARY KEY, currency TEXT, amount REAL)"
        )
        connection.executemany(
            "INSERT INTO virtual_portfolio VALUES (?, ?, ?)",
            [(1, "BRL", 9000.0), (2, "BTC", 0.01)],
        )
    before = source.read_bytes()

    inspected = migrate_sqlite_to_postgres.inspect_source(source)
    migrated = migrate_sqlite_to_postgres.migrate(source, replace=True)

    assert inspected["klines"] == 1
    assert inspected["paper_position_reconciliations"] == 0
    assert migrated["klines"] == 1
    assert migrated["virtual_portfolio"] == 2
    assert source.read_bytes() == before
    assert repository.get_virtual_portfolio() == {"BRL": 9000.0, "BTC": 0.01}
