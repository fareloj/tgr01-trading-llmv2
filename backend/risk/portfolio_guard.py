import math
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.core import database, repository


DEFAULT_TRADING_TIMEZONE = "America/Sao_Paulo"


def trading_day_start(timestamp: int, timezone_name: str | None = None) -> int:
    """Return the Unix timestamp for local midnight in the configured trading zone."""
    zone_name = timezone_name or os.getenv("TRADING_TIMEZONE", DEFAULT_TRADING_TIMEZONE)
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"Fuso horario de trading invalido: {zone_name!r}.") from error
    local_time = datetime.fromtimestamp(int(timestamp), tz=zone)
    return int(local_time.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def capture_daily_equity(
    current_price: float,
    *,
    timestamp: int | None = None,
    asset: str = "BTC/BRL",
    source: str = "live_cycle",
    timezone_name: str | None = None,
) -> dict:
    """Persist and return a mark-to-market snapshot with the day's reference equity."""
    observed_at = int(timestamp if timestamp is not None else time.time())
    try:
        mark_price = float(current_price)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Preco de marcacao do portfolio deve ser numerico.") from error
    if not math.isfinite(mark_price) or mark_price <= 0:
        raise ValueError("Preco de marcacao do portfolio deve ser finito e positivo.")

    day_start = trading_day_start(observed_at, timezone_name)
    with database.engine.begin() as connection:
        portfolio = repository.get_virtual_portfolio(connection=connection, for_update=True)
        if {"BRL", "BTC"} - set(portfolio):
            raise RuntimeError("Portfolio incompleto para captura de equity.")

        brl_balance = float(portfolio["BRL"])
        btc_balance = float(portfolio["BTC"])
        if not all(math.isfinite(value) and value >= 0 for value in (brl_balance, btc_balance)):
            raise RuntimeError("Portfolio contem saldo nao finito ou negativo.")

        equity_brl = brl_balance + (btc_balance * mark_price)
        if not math.isfinite(equity_brl) or equity_brl <= 0:
            raise RuntimeError("Equity paper deve ser finita e positiva.")

        snapshot_id = repository.add_equity_snapshot(
            {
                "timestamp": observed_at,
                "asset": asset,
                "mark_price": mark_price,
                "brl_balance": brl_balance,
                "btc_balance": btc_balance,
                "equity_brl": equity_brl,
                "source": source,
            },
            connection=connection,
        )
        reference = repository.get_first_equity_snapshot(
            asset,
            day_start,
            observed_at,
            connection=connection,
        )

    if reference is None or float(reference["equity_brl"]) <= 0:
        raise RuntimeError("Baseline diario de equity nao pode ser determinado.")
    reference_equity = float(reference["equity_brl"])
    drawdown_pct = max(0.0, ((reference_equity - equity_brl) / reference_equity) * 100.0)
    exposure_pct = ((btc_balance * mark_price) / equity_brl) * 100.0
    return {
        "equity_snapshot_id": snapshot_id,
        "equity_snapshot_timestamp": observed_at,
        "equity_brl": round(equity_brl, 8),
        "daily_reference_equity_brl": round(reference_equity, 8),
        "daily_reference_timestamp": int(reference["timestamp"]),
        "daily_drawdown_percentage": round(drawdown_pct, 8),
        "current_exposure_percentage": round(exposure_pct, 8),
        "is_in_drawdown": drawdown_pct > 0.0,
    }


def enrich_payload_with_daily_equity(
    payload: dict,
    *,
    max_daily_drawdown: float,
    timestamp: int | None = None,
) -> dict:
    current_price = payload.get("technical_context", {}).get("current_price")
    state = capture_daily_equity(current_price, timestamp=timestamp)
    portfolio = payload.setdefault("portfolio_context", {})
    portfolio.update(state)
    portfolio["daily_drawdown_limit_percentage"] = float(max_daily_drawdown)
    return payload
