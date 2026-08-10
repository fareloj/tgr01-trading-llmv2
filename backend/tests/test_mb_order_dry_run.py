import time
from decimal import Decimal

from backend.execution.mb_order_dry_run import validate_market_buy, validate_market_sell
from backend.execution.mb_private_client import (
    Balance,
    MBCredentials,
    MBReadOnlyClient,
    OrderBook,
    OrderBookLevel,
    TradingFees,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, get_payloads):
        self.get_payloads = list(get_payloads)
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse({"access_token": "secret-token", "expires_in": 3600})

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return FakeResponse(self.get_payloads.pop(0))


def _credentials():
    return MBCredentials(
        base_url="https://api.mercadobitcoin.net/api/v4",
        client_id="id",
        client_secret="secret",
        account_id="account",
    )


def _market():
    balances = {
        "BRL": Balance("BRL", Decimal("100"), Decimal("0"), Decimal("100")),
        "BTC": Balance("BTC", Decimal("0.01"), Decimal("0"), Decimal("0.01")),
    }
    fees = TradingFees("BTC", "BRL", Decimal("0.003"), Decimal("0.007"))
    rules = {
        "min-cost": Decimal("0.90"),
        "max-cost": Decimal("1000000"),
        "min-volume": Decimal("0.00000150"),
        "max-volume": Decimal("45"),
        "round-lot": Decimal("0.00000001"),
    }
    book = OrderBook(
        asks=(OrderBookLevel(Decimal("400000"), Decimal("1")),),
        bids=(OrderBookLevel(Decimal("399000"), Decimal("1")),),
        timestamp=1,
    )
    return balances, fees, rules, book


def test_read_only_client_uses_only_oauth_post_and_gets():
    session = FakeSession(
        [[{"symbol": "BRL", "available": "12.34", "on_hold": "0", "total": "12.34"}]]
    )
    client = MBReadOnlyClient(_credentials(), session=session)

    balances = client.list_balances()

    assert balances["BRL"].available == Decimal("12.34")
    assert len(session.posts) == 1
    assert session.posts[0][0] == "https://api.mercadobitcoin.net/oauth2/token"
    assert session.posts[0][1]["data"]["grant_type"] == "client_credentials"
    assert session.posts[0][1]["data"]["scope"] == "global"
    assert len(session.gets) == 1
    assert session.gets[0][0].endswith("/api/v4/accounts/account/balances")
    assert not hasattr(client, "place_order")
    assert not hasattr(client, "cancel_order")
    assert not hasattr(client, "withdraw")


def test_market_buy_payload_and_estimate_are_valid():
    result = validate_market_buy(Decimal("10"), *_market())

    assert result.valid is True
    assert result.payload == {"type": "market", "side": "buy", "cost": "10"}
    assert result.reference_price == Decimal("400000")
    assert result.estimated_gross == Decimal("0.000025")


def test_market_sell_payload_and_estimate_are_valid():
    result = validate_market_sell(Decimal("0.001"), *_market())

    assert result.valid is True
    assert result.payload == {"type": "market", "side": "sell", "qty": "0.00100000"}
    assert result.reference_price == Decimal("399000")
    assert result.estimated_gross == Decimal("399.00000000")


def test_dry_run_blocks_insufficient_balances_and_exchange_limits():
    balances, fees, rules, book = _market()

    assert validate_market_buy(Decimal("100"), balances, fees, rules, book).valid is False
    assert validate_market_sell(Decimal("0.02"), balances, fees, rules, book).valid is False
    assert validate_market_buy(Decimal("0.50"), balances, fees, rules, book).reason == "cost fora dos limites do par"
    assert validate_market_sell(Decimal("0.00000001"), balances, fees, rules, book).reason == "qty fora dos limites do par"
    assert validate_market_sell(Decimal("NaN"), balances, fees, rules, book).reason == "qty deve ser positiva"


def test_client_rejects_stale_orderbook():
    session = FakeSession(
        [{"asks": [["400000", "1"]], "bids": [["399000", "1"]], "timestamp": 1}]
    )
    client = MBReadOnlyClient(_credentials(), session=session)

    try:
        client.get_orderbook()
        raised = False
    except RuntimeError as exc:
        raised = "fora da janela segura" in str(exc)

    assert raised


def test_client_accepts_nanosecond_orderbook_timestamp():
    timestamp_ns = int(time.time() * 1_000_000_000)
    session = FakeSession(
        [{"asks": [["400000", "1"]], "bids": [["399000", "1"]], "timestamp": timestamp_ns}]
    )
    client = MBReadOnlyClient(_credentials(), session=session)

    assert client.get_orderbook().timestamp == timestamp_ns
