import pytest

from backend.execution import market_data_gateway
from backend.execution.market_data_gateway import MBDataGateway, StaleDataError


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _payload(timestamp=1000):
    return {
        "t": [timestamp],
        "o": [100.0],
        "h": [110.0],
        "l": [90.0],
        "c": [105.0],
        "v": [1.0],
    }


def test_gateway_uses_countback_and_returns_latest_candle(monkeypatch):
    captured = {}

    def get(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response(_payload())

    monkeypatch.setattr(market_data_gateway.time, "time", lambda: 1100.0)
    monkeypatch.setattr(market_data_gateway.requests, "get", get)

    candle = MBDataGateway().fetch_latest_kline()

    assert candle["close"] == 105.0
    assert captured["params"]["countback"] == 5
    assert "from" not in captured["params"]


def test_gateway_rejects_empty_candle_response(monkeypatch):
    monkeypatch.setattr(market_data_gateway.time, "time", lambda: 1100.0)
    monkeypatch.setattr(
        market_data_gateway.requests,
        "get",
        lambda *args, **kwargs: Response({key: [] for key in ("t", "o", "h", "l", "c", "v")}),
    )

    with pytest.raises(StaleDataError, match="vazios"):
        MBDataGateway().fetch_latest_kline()


def test_gateway_rejects_stale_and_future_candles(monkeypatch):
    monkeypatch.setattr(market_data_gateway.time, "time", lambda: 2000.0)
    monkeypatch.setattr(market_data_gateway.requests, "get", lambda *args, **kwargs: Response(_payload(500)))
    with pytest.raises(StaleDataError, match="atrasado"):
        MBDataGateway().fetch_latest_kline()

    monkeypatch.setattr(market_data_gateway.requests, "get", lambda *args, **kwargs: Response(_payload(2100)))
    with pytest.raises(StaleDataError, match="futuro"):
        MBDataGateway().fetch_latest_kline()


def test_gateway_rejects_inconsistent_or_invalid_market_data(monkeypatch):
    monkeypatch.setattr(market_data_gateway.time, "time", lambda: 1100.0)
    inconsistent = _payload()
    inconsistent["v"] = []
    monkeypatch.setattr(market_data_gateway.requests, "get", lambda *args, **kwargs: Response(inconsistent))
    with pytest.raises(RuntimeError, match="tamanhos inconsistentes"):
        MBDataGateway().fetch_latest_kline()

    invalid = _payload()
    invalid["l"] = [120.0]
    monkeypatch.setattr(market_data_gateway.requests, "get", lambda *args, **kwargs: Response(invalid))
    with pytest.raises(RuntimeError, match="Preco malformado"):
        MBDataGateway().fetch_latest_kline()
