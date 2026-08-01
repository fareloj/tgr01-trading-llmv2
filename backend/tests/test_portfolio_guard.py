from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.core import repository
from backend.risk.portfolio_guard import capture_daily_equity, trading_day_start


SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def _timestamp(day: int, hour: int) -> int:
    return int(datetime(2026, 8, day, hour, tzinfo=SAO_PAULO).timestamp())


def test_daily_equity_uses_first_snapshot_as_reference_and_resets_next_day():
    first = capture_daily_equity(100_000.0, timestamp=_timestamp(1, 9))
    assert first["equity_brl"] == 10_000.0
    assert first["daily_reference_equity_brl"] == 10_000.0
    assert first["daily_drawdown_percentage"] == 0.0
    assert first["is_in_drawdown"] is False

    repository.update_virtual_portfolio("BRL", 9_000.0)
    second = capture_daily_equity(100_000.0, timestamp=_timestamp(1, 15))
    assert second["daily_reference_timestamp"] == _timestamp(1, 9)
    assert second["daily_reference_equity_brl"] == 10_000.0
    assert second["daily_drawdown_percentage"] == 10.0
    assert second["is_in_drawdown"] is True

    next_day = capture_daily_equity(100_000.0, timestamp=_timestamp(2, 9))
    assert next_day["daily_reference_equity_brl"] == 9_000.0
    assert next_day["daily_drawdown_percentage"] == 0.0


def test_daily_equity_marks_btc_and_recomputes_exposure():
    repository.update_virtual_portfolio("BRL", 5_000.0)
    repository.update_virtual_portfolio("BTC", 0.05)

    state = capture_daily_equity(100_000.0, timestamp=_timestamp(1, 10))

    assert state["equity_brl"] == 10_000.0
    assert state["current_exposure_percentage"] == 50.0


def test_portfolio_guard_rejects_invalid_price_and_timezone():
    with pytest.raises(ValueError, match="finito e positivo"):
        capture_daily_equity(float("nan"), timestamp=_timestamp(1, 10))
    with pytest.raises(RuntimeError, match="Fuso horario"):
        trading_day_start(_timestamp(1, 10), "Invalid/Trading_Zone")
