import os
import re
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from backend.core import database
from backend.core.db_models import metadata

# Banco de teste dedicado. Se TEST_DATABASE_URL nao existir, deriva um banco
# irmao terminado em _test. A suite nunca pode apontar para o banco da aplicacao.
_APP_URL = os.getenv("DATABASE_URL")
_EXPLICIT_TEST_URL = os.getenv("TEST_DATABASE_URL")


def _normalize(url: str) -> str:
    if url and url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _derive_test_url(app_url: str | None) -> str | None:
    if not app_url:
        return None
    url = make_url(_normalize(app_url))
    database_name = url.database or "tgr01"
    return url.set(database=f"{database_name}_test").render_as_string(hide_password=False)


def _resolve_test_url() -> str | None:
    if not _EXPLICIT_TEST_URL:
        return _derive_test_url(_APP_URL)
    explicit = _normalize(_EXPLICIT_TEST_URL)
    if _APP_URL and make_url(explicit).database == make_url(_normalize(_APP_URL)).database:
        return _derive_test_url(_APP_URL)
    return explicit


_TEST_URL = _resolve_test_url()


def _ensure_test_database(url: str) -> None:
    parsed = make_url(url)
    test_name = parsed.database or ""
    if not re.fullmatch(r"[A-Za-z0-9_]+_test", test_name):
        raise RuntimeError(f"Nome de banco de teste inseguro: {test_name!r}. Use um nome terminado em _test.")
    admin_url = parsed.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with admin_engine.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": test_name})
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{test_name}"'))
    finally:
        admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    if not _TEST_URL or not _TEST_URL.startswith("postgresql"):
        raise RuntimeError(
            "TEST_DATABASE_URL (ou DATABASE_URL para derivar *_test) deve apontar para PostgreSQL.\n"
            "Suba o Docker (docker compose up -d) e configure o .env.\n"
            "Rodar a suite em SQLite NAO valida a migracao e foi removido de proposito."
        )
    app_database = make_url(_normalize(_APP_URL)).database if _APP_URL else None
    test_database = make_url(_TEST_URL).database
    if app_database == test_database:
        raise RuntimeError("TEST_DATABASE_URL nao pode apontar para o banco da aplicacao.")

    _ensure_test_database(_TEST_URL)
    test_engine = create_engine(_TEST_URL, pool_pre_ping=True)
    original_engine = database.engine

    # O codigo usa um unico namespace de pacote: backend.core.database.
    database.engine = test_engine

    metadata.drop_all(test_engine)
    metadata.create_all(test_engine)
    try:
        yield
    finally:
        metadata.drop_all(test_engine)
        database.engine = original_engine
        test_engine.dispose()


@pytest.fixture(scope="function", autouse=True)
def clean_db():
    # Garante slate limpo por teste, contra PostgreSQL.
    metadata.drop_all(database.engine)
    metadata.create_all(database.engine)
    with database.engine.begin() as conn:
        vp_t = metadata.tables["virtual_portfolio"]
        conn.execute(vp_t.insert(), [
            {"currency": "BRL", "amount": 10000.0},
            {"currency": "BTC", "amount": 0.0},
        ])
