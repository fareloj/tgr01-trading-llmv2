"""Read-only Mercado Bitcoin private API client.

This module intentionally exposes no order creation, cancellation, withdrawal,
or transfer method. The only POST request is the OAuth2 token exchange.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from backend.core.tls import configure_native_ca_store


class MBPrivateAPIError(RuntimeError):
    """Raised when authentication or a read-only API request fails."""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.lower().startswith("replace_"):
        raise MBPrivateAPIError(f"{name} nao esta configurado.")
    return value


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MBPrivateAPIError(f"Campo decimal invalido em {field}.") from exc
    if not parsed.is_finite() or parsed < 0:
        raise MBPrivateAPIError(f"Campo decimal invalido em {field}.")
    return parsed


@dataclass(frozen=True)
class MBCredentials:
    base_url: str
    client_id: str
    client_secret: str
    account_id: str

    @classmethod
    def from_env(cls) -> "MBCredentials":
        return cls(
            base_url=os.getenv(
                "MB_API_BASE_URL", "https://api.mercadobitcoin.net/api/v4"
            ).rstrip("/"),
            client_id=_required_env("MB_CLIENT_ID"),
            client_secret=_required_env("MB_CLIENT_SECRET"),
            account_id=_required_env("MB_ACCOUNT_ID"),
        )


@dataclass(frozen=True)
class Balance:
    symbol: str
    available: Decimal
    on_hold: Decimal
    total: Decimal


@dataclass(frozen=True)
class TradingFees:
    base: str
    quote: str
    maker_fee: Decimal
    taker_fee: Decimal


@dataclass(frozen=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class OrderBook:
    asks: tuple[OrderBookLevel, ...]
    bids: tuple[OrderBookLevel, ...]
    timestamp: int


class MBReadOnlyClient:
    """Authenticated client restricted to OAuth and HTTP GET requests."""

    def __init__(
        self,
        credentials: MBCredentials,
        *,
        session: requests.Session | None = None,
        timeout: float = 10.0,
    ) -> None:
        configure_native_ca_store()
        self.credentials = credentials
        self.session = session or requests.Session()
        self.timeout = timeout
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    @property
    def token_url(self) -> str:
        parts = urlsplit(self.credentials.base_url)
        return urlunsplit((parts.scheme, parts.netloc, "/oauth2/token", "", ""))

    def authenticate(self) -> None:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return
        try:
            response = self.session.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "scope": "global",
                    "client_id": self.credentials.client_id,
                    "client_secret": self.credentials.client_secret,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MBPrivateAPIError("Falha de rede durante autenticacao no MB.") from exc
        if response.status_code != 200:
            raise MBPrivateAPIError(
                f"Autenticacao no MB falhou com HTTP {response.status_code}."
            )
        payload = self._json_object(response, "oauth2/token")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise MBPrivateAPIError("Resposta OAuth sem access_token valido.")
        try:
            expires_in = max(1, int(payload.get("expires_in", 300)))
        except (TypeError, ValueError) as exc:
            raise MBPrivateAPIError("Resposta OAuth com expires_in invalido.") from exc
        self._access_token = token
        self._token_expires_at = time.monotonic() + max(1, expires_in - 30)

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        self.authenticate()
        try:
            response = self.session.get(
                f"{self.credentials.base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MBPrivateAPIError(f"Falha de rede na leitura MB {path}.") from exc
        if response.status_code != 200:
            raise MBPrivateAPIError(
                f"Leitura MB {path} falhou com HTTP {response.status_code}."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise MBPrivateAPIError(f"Resposta JSON invalida em {path}.") from exc

    @staticmethod
    def _json_object(response: requests.Response, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise MBPrivateAPIError(f"Resposta JSON invalida em {operation}.") from exc
        if not isinstance(payload, dict):
            raise MBPrivateAPIError(f"Resposta inesperada em {operation}.")
        return payload

    def list_accounts(self) -> list[dict[str, Any]]:
        payload = self._get("/accounts")
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise MBPrivateAPIError("Resposta inesperada ao listar contas.")
        return payload

    def list_balances(self) -> dict[str, Balance]:
        payload = self._get(f"/accounts/{self.credentials.account_id}/balances")
        if not isinstance(payload, list):
            raise MBPrivateAPIError("Resposta inesperada ao listar saldos.")
        balances: dict[str, Balance] = {}
        for index, row in enumerate(payload):
            if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
                raise MBPrivateAPIError(f"Saldo invalido na posicao {index}.")
            symbol = row["symbol"].upper()
            balances[symbol] = Balance(
                symbol=symbol,
                available=_decimal(row.get("available"), f"balances[{index}].available"),
                on_hold=_decimal(row.get("on_hold"), f"balances[{index}].on_hold"),
                total=_decimal(row.get("total"), f"balances[{index}].total"),
            )
        return balances

    def get_trading_fees(self, symbol: str = "BTC-BRL") -> TradingFees:
        payload = self._get(
            f"/accounts/{self.credentials.account_id}/{symbol}/fees"
        )
        if not isinstance(payload, dict):
            raise MBPrivateAPIError("Resposta inesperada ao consultar taxas.")
        fees = TradingFees(
            base=str(payload.get("base", "")).upper(),
            quote=str(payload.get("quote", "")).upper(),
            maker_fee=_decimal(payload.get("maker_fee"), "maker_fee"),
            taker_fee=_decimal(payload.get("taker_fee"), "taker_fee"),
        )
        if fees.maker_fee > 1 or fees.taker_fee > 1:
            raise MBPrivateAPIError("Taxa de negociacao fora do intervalo 0..1.")
        return fees

    def get_symbol_rules(self, symbol: str = "BTC-BRL") -> dict[str, Decimal]:
        payload = self._get("/symbols", params={"symbols": symbol})
        if not isinstance(payload, dict):
            raise MBPrivateAPIError("Resposta inesperada ao consultar regras do par.")
        symbols = payload.get("symbol")
        if not isinstance(symbols, list) or symbol not in symbols:
            raise MBPrivateAPIError(f"Par {symbol} ausente na resposta /symbols.")
        index = symbols.index(symbol)
        result: dict[str, Decimal] = {}
        for field in ("min-cost", "max-cost", "min-volume", "max-volume", "round-lot"):
            values = payload.get(field)
            if not isinstance(values, list) or index >= len(values):
                raise MBPrivateAPIError(f"Regra {field} ausente para {symbol}.")
            result[field] = _decimal(values[index], field)
        return result

    def get_orderbook(self, symbol: str = "BTC-BRL", limit: int = 100) -> OrderBook:
        payload = self._get(f"/{symbol}/orderbook", params={"limit": str(limit)})
        if not isinstance(payload, dict):
            raise MBPrivateAPIError("Resposta inesperada ao consultar orderbook.")

        def levels(side: str) -> tuple[OrderBookLevel, ...]:
            raw = payload.get(side)
            if not isinstance(raw, list) or not raw:
                raise MBPrivateAPIError(f"Orderbook sem niveis em {side}.")
            parsed: list[OrderBookLevel] = []
            for index, level in enumerate(raw):
                if not isinstance(level, (list, tuple)) or len(level) < 2:
                    raise MBPrivateAPIError(f"Nivel invalido em {side}[{index}].")
                price = _decimal(level[0], f"{side}[{index}].price")
                quantity = _decimal(level[1], f"{side}[{index}].quantity")
                if price == 0 or quantity == 0:
                    raise MBPrivateAPIError(f"Nivel zerado em {side}[{index}].")
                parsed.append(OrderBookLevel(price=price, quantity=quantity))
            return tuple(parsed)

        try:
            timestamp = int(payload.get("timestamp", 0))
        except (TypeError, ValueError) as exc:
            raise MBPrivateAPIError("Timestamp invalido no orderbook.") from exc
        timestamp_seconds = timestamp
        for threshold in (10**17, 10**14, 10**11):
            if timestamp_seconds >= threshold:
                timestamp_seconds //= 1000
        age_seconds = int(time.time()) - timestamp_seconds
        if age_seconds < -30 or age_seconds > 120:
            raise MBPrivateAPIError(
                f"Orderbook fora da janela segura: age={age_seconds}s."
            )
        return OrderBook(asks=levels("asks"), bids=levels("bids"), timestamp=timestamp)
