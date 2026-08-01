from backend.core import database, repository
from backend.tests.evaluate_decisions import assess_future_price, fetch_future_price


def _kline(timestamp: int, close: float = 100.0) -> dict:
    return {
        "asset": "BTC/BRL",
        "timeframe": "1m",
        "timestamp": timestamp,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
    }


def test_future_price_accepts_only_candle_near_target_horizon():
    decision_timestamp = 1_000
    target = decision_timestamp + 300
    repository.add_klines([_kline(target + 45, 101.0), _kline(target + 600, 110.0)])

    with database.engine.connect() as connection:
        status, future = assess_future_price(connection, decision_timestamp, 5, 90)

    assert status == "matured"
    assert future == {"timestamp": target + 45, "close": 101.0}


def test_future_price_reports_data_gap_instead_of_using_distant_candle():
    decision_timestamp = 2_000
    target = decision_timestamp + 300
    repository.add_klines([_kline(target + 600, 110.0)])

    with database.engine.connect() as connection:
        status, future = assess_future_price(connection, decision_timestamp, 5, 90)
        raw = fetch_future_price(connection, decision_timestamp, 5, 90)

    assert status == "data_gap"
    assert future is None
    assert raw is None


def test_future_price_reports_not_matured_when_market_has_not_reached_target():
    decision_timestamp = 3_000
    target = decision_timestamp + 300
    repository.add_klines([_kline(target - 1, 99.0)])

    with database.engine.connect() as connection:
        status, future = assess_future_price(connection, decision_timestamp, 5, 90)

    assert status == "not_matured"
    assert future is None
