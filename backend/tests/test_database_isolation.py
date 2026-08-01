import ast
from pathlib import Path

from sqlalchemy.engine import make_url

from backend.core import database


PROJECT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_DIR / "backend"
SQLITE_ALLOWLIST = {BACKEND_DIR / "tests" / "migrate_sqlite_to_postgres.py"}
LEGACY_DATABASE_FILENAME = "trading_v2" + ".db"


def test_pytest_engine_is_isolated_from_application_database():
    app_database = make_url(database.DATABASE_URL).database
    test_database = make_url(str(database.engine.url)).database

    assert test_database != app_database
    assert test_database and test_database.endswith("_test")


def test_active_python_modules_do_not_import_sqlite():
    offenders = []
    for path in BACKEND_DIR.rglob("*.py"):
        if path in SQLITE_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(alias.name == "sqlite3" for alias in node.names):
                offenders.append(str(path.relative_to(PROJECT_DIR)))
            if isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
                offenders.append(str(path.relative_to(PROJECT_DIR)))

    assert offenders == []


def test_legacy_database_filename_is_confined_to_migrator():
    offenders = []
    for path in BACKEND_DIR.rglob("*.py"):
        if path in SQLITE_ALLOWLIST:
            continue
        if LEGACY_DATABASE_FILENAME in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(PROJECT_DIR)))

    assert offenders == []
