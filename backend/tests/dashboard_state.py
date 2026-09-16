import argparse
import json
import sys
import time
from pathlib import Path

from sqlalchemy import func, select

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
REPORTS_DIR = BACKEND_DIR / "reports"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database
from backend.core.db_models import (
    equity_snapshots,
    klines,
    news,
    paper_position_reconciliations,
    paper_position_state,
    rag_chunks,
    rag_documents,
    rag_retrieval_logs,
    system_health,
    trade_logs,
    virtual_portfolio,
)
from backend.agents.model_config import resolve_multi_agent_model_config
from backend.core.clock_sync import check_clock_skew
from backend.core.market_policy import LIVE_MAX_EXPOSURE_PCT, MARKET_DATA_MAX_AGE_SECONDS
from backend.core.runtime_safety import MAX_FUTURE_HEARTBEAT_SECONDS, REQUIRED_WORKERS
from backend.features.payload_builder import NEWS_STALE_SECONDS
from backend.execution.paper_simulator import PaperExecutionConfig
from backend.rag.external_client import ExternalRagClient
from backend.risk.portfolio_guard import trading_day_start
from backend.risk.risk_manager import RiskManager


def build_freshness_policy() -> dict:
    """Expose the freshness ceilings the pipeline itself enforces.

    The console must judge a candle or news age against the same thresholds the
    runtime uses, not against a guess, so a stale reading is never shown green.
    """
    return {
        "market_data_stale_threshold_seconds": MARKET_DATA_MAX_AGE_SECONDS,
        "news_stale_threshold_seconds": NEWS_STALE_SECONDS,
    }


def build_execution_config() -> dict:
    """Expose the paper cost assumptions so the console can show the real hurdle.

    The diagnostic showed the project's fee assumption dominates the horizon
    decision, so the operator needs the numbers visible rather than implied.
    Slippage is ATR-derived at execution time; these are the configured bounds.
    """
    config = PaperExecutionConfig.from_env()
    one_side = config.fee_rate + config.min_slippage_rate
    return {
        "fee_rate": config.fee_rate,
        "min_slippage_rate": config.min_slippage_rate,
        "max_slippage_rate": config.max_slippage_rate,
        "atr_slippage_factor": config.atr_slippage_factor,
        "one_side_cost_pct": round(one_side * 100, 4),
        "buy_round_trip_cost_pct": round(2 * one_side * 100, 4),
        "sell_exit_cost_pct": round(one_side * 100, 4),
    }


def build_execution_config_safe() -> dict:
    """`build_execution_config` with a fail-closed wrapper.

    A malformed PAPER_* value makes PaperExecutionConfig raise. The dashboard is
    a read-only view and must still render, so the failure is reported in-band
    instead of taking the whole operator console down.
    """
    try:
        return build_execution_config()
    except (ValueError, TypeError) as error:
        return {"error": f"{type(error).__name__}: {error}"}


def build_model_roles() -> dict:
    """Expose the resolved role models and their budgets, without credentials."""
    try:
        config = resolve_multi_agent_model_config()
    except ValueError as error:
        return {"error": str(error), "roles": {}, "enabled": False, "shadow_mode": True}
    roles = {}
    for name, role_model in config.roles().items():
        roles[name] = {
            "model": role_model.model,
            "provider": role_model.provider,
            "temperature": role_model.temperature,
            "max_tokens": role_model.max_tokens,
            "reasoning_effort": role_model.reasoning_effort,
        }
    return {
        "enabled": config.enabled,
        "shadow_mode": config.shadow_mode,
        "may_influence_paper_decisions": config.may_influence_paper_decisions,
        "roles": roles,
    }


def build_risk_gates() -> dict:
    """Expose the deterministic gates the LLM proposal must satisfy.

    Built from the same policy constant the live pipeline uses
    (`LIVE_MAX_EXPOSURE_PCT`), not from the RiskManager class default, so the
    console can never advertise a limit the runtime does not enforce.
    """
    manager = RiskManager(max_exposure=LIVE_MAX_EXPOSURE_PCT)
    return {
        "minimum_conviction_pct": manager.MINIMUM_CONVICTION,
        "no_news_minimum_conviction_pct": manager.NO_NEWS_MINIMUM_CONVICTION,
        "minimum_hybrid_confidence_pct": round(manager.MINIMUM_HYBRID_CONFIDENCE * 100, 4),
        "max_daily_drawdown_pct": manager.max_daily_drawdown,
        "max_exposure_pct": manager.max_exposure,
        "cooldown_minutes": manager.cooldown_minutes,
    }


def get_external_rag_health() -> dict:
    return ExternalRagClient(timeout_seconds=1.5).health()


def parse_snapshot(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _as_dict(row) -> dict:
    return dict(row._mapping)


def fetch_dashboard_state(recent_limit: int = 12) -> dict:
    """Build the UI state from the same PostgreSQL database used by the pipeline."""
    now = int(time.time())
    with database.engine.connect() as conn:
        worker_rows = list(conn.execute(
            select(system_health.c.worker_name, system_health.c.last_heartbeat).order_by(
                system_health.c.worker_name
            )
        ))
        worker_map = {row.worker_name: int(row.last_heartbeat) for row in worker_rows}
        workers = {}
        for worker_name, limit in REQUIRED_WORKERS.items():
            heartbeat = worker_map.get(worker_name)
            age = now - heartbeat if heartbeat is not None else None
            if heartbeat is None:
                status = "missing"
            elif age < -MAX_FUTURE_HEARTBEAT_SECONDS:
                status = "future"
            elif age > limit:
                status = "stale"
            else:
                status = "healthy"
            workers[worker_name] = {
                "last_heartbeat": heartbeat,
                "age_seconds": age,
                "status": status,
            }

        latest_kline = conn.execute(
            select(klines.c.timestamp, klines.c.close)
            .where(klines.c.asset == "BTC/BRL", klines.c.timeframe == "1m")
            .order_by(klines.c.timestamp.desc())
            .limit(1)
        ).first()
        latest_news = conn.execute(
            select(news.c.timestamp, news.c.source, news.c.headline)
            .order_by(news.c.timestamp.desc())
            .limit(1)
        ).first()
        portfolio = {
            row.currency: float(row.amount)
            for row in conn.execute(
                select(virtual_portfolio.c.currency, virtual_portfolio.c.amount)
            )
        }
        day_start = trading_day_start(now)
        first_equity_snapshot = conn.execute(
            select(equity_snapshots)
            .where(
                equity_snapshots.c.asset == "BTC/BRL",
                equity_snapshots.c.timestamp >= day_start,
                equity_snapshots.c.timestamp <= now,
            )
            .order_by(equity_snapshots.c.timestamp.asc(), equity_snapshots.c.id.asc())
            .limit(1)
        ).mappings().first()
        latest_equity_snapshot = conn.execute(
            select(equity_snapshots)
            .where(
                equity_snapshots.c.asset == "BTC/BRL",
                equity_snapshots.c.timestamp >= day_start,
                equity_snapshots.c.timestamp <= now,
            )
            .order_by(equity_snapshots.c.timestamp.desc(), equity_snapshots.c.id.desc())
            .limit(1)
        ).mappings().first()
        position_row = conn.execute(
            select(paper_position_state).where(paper_position_state.c.asset == "BTC/BRL")
        ).mappings().first()
        reconciliation_row = conn.execute(
            select(paper_position_reconciliations)
            .where(paper_position_reconciliations.c.asset == "BTC/BRL")
            .order_by(
                paper_position_reconciliations.c.timestamp.desc(),
                paper_position_reconciliations.c.id.desc(),
            )
            .limit(1)
        ).mappings().first()
        rag = {
            "documents": int(conn.scalar(select(func.count()).select_from(rag_documents)) or 0),
            "chunks": int(conn.scalar(select(func.count()).select_from(rag_chunks)) or 0),
            "retrievals": int(conn.scalar(select(func.count()).select_from(rag_retrieval_logs)) or 0),
        }
        log_rows = conn.execute(
            select(trade_logs).order_by(trade_logs.c.id.desc()).limit(recent_limit)
        )
        logs = []
        for row in log_rows:
            item = _as_dict(row)
            raw_snapshot = item.pop("payload_snapshot_json", None)
            item["snapshot"] = parse_snapshot(raw_snapshot)
            logs.append(item)

    latest_price = float(latest_kline.close) if latest_kline else 0.0
    btc_balance = portfolio.get("BTC", 0.0)
    equity = portfolio.get("BRL", 0.0) + btc_balance * latest_price
    # Without a price the BTC leg cannot be valued. If the account holds BTC, the
    # exposure is unknown rather than zero: reporting 0% would understate risk.
    if latest_kline is None and btc_balance > 0:
        exposure = None
    else:
        exposure = (btc_balance * latest_price / equity * 100.0) if equity else 0.0
    daily_reference_equity = (
        float(first_equity_snapshot["equity_brl"]) if first_equity_snapshot else None
    )
    daily_drawdown = None
    if daily_reference_equity and equity > 0:
        daily_drawdown = max(0.0, ((daily_reference_equity - equity) / daily_reference_equity) * 100.0)
    clock = check_clock_skew(timeout=2.0)
    reports = []
    if REPORTS_DIR.exists():
        files = (path for path in REPORTS_DIR.iterdir() if path.is_file())
        for path in sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)[:8]:
            stat = path.stat()
            reports.append(
                {"name": path.name, "size_bytes": stat.st_size, "modified_at": int(stat.st_mtime)}
            )

    entry_evaluation = read_json_file(REPORTS_DIR / "last_entry_decisions.json")
    if isinstance(entry_evaluation, dict):
        entry_evaluation["db_path"] = database.get_database_label()

    position = dict(position_row) if position_row else None
    if position is not None:
        position["reconciliation"] = None
        if reconciliation_row:
            position["reconciliation"] = {
                "id": int(reconciliation_row["id"]),
                "timestamp": int(reconciliation_row["timestamp"]),
                "method": reconciliation_row["method"],
                "source_log_ids": json.loads(reconciliation_row["source_log_ids_json"]),
            }

    return {
        "generated_at": now,
        "db_path": database.get_database_label(),
        "database": {"backend": "PostgreSQL", "label": database.get_database_label()},
        "clock": clock,
        "workers": workers,
        "execution_config": build_execution_config_safe(),
        "freshness_policy": build_freshness_policy(),
        "model_roles": build_model_roles(),
        "risk_gates": build_risk_gates(),
        "latest_kline": {
            "timestamp": int(latest_kline.timestamp) if latest_kline else None,
            "age_seconds": now - int(latest_kline.timestamp) if latest_kline else None,
            # None when there is no candle, so the console can distinguish an
            # absent price from a legitimate zero close.
            "close": float(latest_kline.close) if latest_kline else None,
        },
        "latest_news": {
            "timestamp": int(latest_news.timestamp) if latest_news else None,
            "age_seconds": now - int(latest_news.timestamp) if latest_news else None,
            "source": latest_news.source if latest_news else None,
            "headline": latest_news.headline if latest_news else None,
        },
        "portfolio": {
            "brl": portfolio.get("BRL", 0.0),
            "btc": portfolio.get("BTC", 0.0),
            "equity_brl": round(equity, 2),
            "exposure_pct": round(exposure, 2) if exposure is not None else None,
            "daily_reference_equity_brl": round(daily_reference_equity, 2) if daily_reference_equity else None,
            "daily_drawdown_pct": round(daily_drawdown, 4) if daily_drawdown is not None else None,
            "daily_drawdown_limit_pct": RiskManager().max_daily_drawdown,
            "equity_snapshot_timestamp": (
                int(latest_equity_snapshot["timestamp"]) if latest_equity_snapshot else None
            ),
        },
        "position": position,
        "rag": rag,
        "external_rag": get_external_rag_health(),
        "reports": reports,
        "entry_evaluation": entry_evaluation,
        "logs": logs,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Emit PostgreSQL operational dashboard state as JSON.")
    parser.add_argument("--recent-limit", type=int, default=12)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fetch_dashboard_state(recent_limit=args.recent_limit), ensure_ascii=False))
