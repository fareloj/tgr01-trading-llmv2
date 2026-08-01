import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv
from sqlalchemy import create_engine, select, func, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.pool import QueuePool
from backend.core.db_models import metadata

BASE_DIR = Path(__file__).resolve().parent.parent

# Load env variables
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL nao definida. Configure o PostgreSQL no .env "
        "(ex: postgresql://user:senha@localhost:5432/tgr01). "
        "O fallback silencioso para SQLite foi REMOVIDO por seguranca (Clean Slate / Postgres-only)."
    )

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

if not DATABASE_URL.startswith("postgresql"):
    raise RuntimeError(
        f"Apenas PostgreSQL e suportado no caminho vivo (Clean Slate). URL recebida: {DATABASE_URL!r}"
    )

# Engine com pool de conexoes real para concorrencia 24/7.
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={"connect_timeout": 5}
)

def get_database_label() -> str:
    """Return the configured PostgreSQL URL without exposing credentials."""
    return make_url(DATABASE_URL).render_as_string(hide_password=True)


def get_db_path() -> str:
    """Compatibility alias for older status payloads; PostgreSQL has no file path."""
    return get_database_label()

def get_connection():
    """Returns a connection context from the SQLAlchemy engine."""
    return engine.connect()

def init_db():
    """Creates the necessary tables and seeds default portfolio values."""
    metadata.create_all(engine)

    vp_t = metadata.tables["virtual_portfolio"]
    with engine.begin() as conn:
        count = conn.scalar(select(func.count()).select_from(vp_t))
        if count == 0:
            conn.execute(vp_t.insert(), [
                {"currency": "BRL", "amount": 10000.0},
                {"currency": "BTC", "amount": 0.0}
            ])
    print(f"[OK] Banco de dados PostgreSQL inicializado em {get_database_label()}")

def get_db_diagnostics() -> dict:
    """Collects database health data using SQLAlchemy Core."""
    diagnostics = {
        "db_path": get_database_label(),
        "db_exists": True,
        "db_size_bytes": 0,
        "tables": list(metadata.tables.keys()),
        "missing_tables": [],
        "counts": {},
        "kline_groups": [],
        "system_health": [],
    }

    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        diagnostics["missing_tables"] = sorted(set(metadata.tables) - existing_tables)
        with engine.connect() as conn:
            for table_name, table_obj in metadata.tables.items():
                if table_name not in existing_tables:
                    diagnostics["counts"][table_name] = None
                    continue
                try:
                    count_val = conn.scalar(select(func.count()).select_from(table_obj))
                    diagnostics["counts"][table_name] = count_val
                except Exception:
                    diagnostics["counts"][table_name] = None

            # Klines groups
            if "klines" in existing_tables:
                klines_t = metadata.tables["klines"]
                q = select(
                    klines_t.c.asset,
                    klines_t.c.timeframe,
                    func.count().label("count"),
                    func.min(klines_t.c.timestamp).label("min_timestamp"),
                    func.max(klines_t.c.timestamp).label("max_timestamp")
                ).group_by(klines_t.c.asset, klines_t.c.timeframe).order_by(func.count().desc())

                res = conn.execute(q)
                diagnostics["kline_groups"] = [
                    {
                        "asset": r[0],
                        "timeframe": r[1],
                        "count": r[2],
                        "min_timestamp": r[3],
                        "max_timestamp": r[4]
                    }
                    for r in res
                ]

            # System health
            if "system_health" in existing_tables:
                sh_t = metadata.tables["system_health"]
                q = select(sh_t.c.worker_name, sh_t.c.last_heartbeat).order_by(sh_t.c.worker_name)

                res = conn.execute(q)
                diagnostics["system_health"] = [
                    {
                        "worker_name": r[0],
                        "last_heartbeat": r[1]
                    }
                    for r in res
                ]
    except Exception as e:
        diagnostics["error"] = str(e)

    return diagnostics

def print_db_diagnostics():
    """Prints a database report using diagnostics."""
    diagnostics = get_db_diagnostics()
    print(f"[DB] Path: {diagnostics.get('db_path')}")
    if "error" in diagnostics:
        print(f"[DB] Connection Error: {diagnostics['error']}")
        return
    print(f"[DB] Counts: {diagnostics['counts']}")
    if diagnostics.get("kline_groups"):
        print("[DB] Klines:")
        for group in diagnostics["kline_groups"]:
            print(
                "     "
                f"{group['asset']} {group['timeframe']} "
                f"count={group['count']} "
                f"range={group['min_timestamp']}..{group['max_timestamp']}"
            )
    if diagnostics.get("system_health"):
        print(f"[DB] Worker health: {diagnostics['system_health']}")

if __name__ == "__main__":
    init_db()
    print_db_diagnostics()
