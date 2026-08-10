"""Build and validate Mercado Bitcoin market-order candidates without sending them."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

from backend.execution.mb_private_client import Balance, OrderBook, TradingFees


@dataclass(frozen=True)
class DryRunResult:
    side: str
    valid: bool
    payload: dict[str, Any]
    reason: str
    reference_price: Decimal | None
    estimated_gross: Decimal | None
    estimated_net: Decimal | None


def _available(balances: dict[str, Balance], symbol: str) -> Decimal:
    balance = balances.get(symbol)
    return balance.available if balance else Decimal("0")


def _consume_quote(levels: tuple, quote_amount: Decimal) -> tuple[Decimal, Decimal]:
    remaining = quote_amount
    base_total = Decimal("0")
    quote_total = Decimal("0")
    for level in levels:
        spend = min(remaining, level.price * level.quantity)
        base_total += spend / level.price
        quote_total += spend
        remaining -= spend
        if remaining == 0:
            break
    if remaining > 0 or base_total == 0:
        raise ValueError("book sem liquidez suficiente")
    return quote_total / base_total, base_total


def _consume_base(levels: tuple, base_amount: Decimal) -> tuple[Decimal, Decimal]:
    remaining = base_amount
    quote_total = Decimal("0")
    sold = Decimal("0")
    for level in levels:
        quantity = min(remaining, level.quantity)
        quote_total += quantity * level.price
        sold += quantity
        remaining -= quantity
        if remaining == 0:
            break
    if remaining > 0 or sold == 0:
        raise ValueError("book sem liquidez suficiente")
    return quote_total / sold, quote_total


def validate_market_buy(
    cost_brl: Decimal,
    balances: dict[str, Balance],
    fees: TradingFees,
    rules: dict[str, Decimal],
    book: OrderBook,
) -> DryRunResult:
    payload = {"type": "market", "side": "buy", "cost": str(cost_brl)}
    if not cost_brl.is_finite() or cost_brl <= 0:
        return DryRunResult("buy", False, payload, "cost deve ser positivo", None, None, None)
    if cost_brl < rules["min-cost"] or cost_brl > rules["max-cost"]:
        return DryRunResult("buy", False, payload, "cost fora dos limites do par", None, None, None)
    conservative_required = cost_brl * (Decimal("1") + fees.taker_fee)
    if conservative_required > _available(balances, "BRL"):
        return DryRunResult("buy", False, payload, "saldo BRL disponivel insuficiente", None, None, None)
    try:
        price, gross_btc = _consume_quote(book.asks, cost_brl)
    except ValueError as exc:
        return DryRunResult("buy", False, payload, str(exc), None, None, None)
    net_btc = gross_btc * (Decimal("1") - fees.taker_fee)
    return DryRunResult(
        "buy", True, payload, "candidato valido; nenhuma ordem enviada", price, gross_btc, net_btc
    )


def validate_market_sell(
    quantity_btc: Decimal,
    balances: dict[str, Balance],
    fees: TradingFees,
    rules: dict[str, Decimal],
    book: OrderBook,
) -> DryRunResult:
    round_lot = rules["round-lot"]
    if not quantity_btc.is_finite() or quantity_btc <= 0:
        payload = {"type": "market", "side": "sell", "qty": str(quantity_btc)}
        return DryRunResult("sell", False, payload, "qty deve ser positiva", None, None, None)
    normalized = quantity_btc.quantize(round_lot, rounding=ROUND_DOWN) if round_lot else quantity_btc
    payload = {"type": "market", "side": "sell", "qty": str(normalized)}
    if normalized <= 0:
        return DryRunResult("sell", False, payload, "qty deve ser positiva", None, None, None)
    if normalized < rules["min-volume"] or normalized > rules["max-volume"]:
        return DryRunResult("sell", False, payload, "qty fora dos limites do par", None, None, None)
    if normalized > _available(balances, "BTC"):
        return DryRunResult("sell", False, payload, "saldo BTC disponivel insuficiente", None, None, None)
    try:
        price, gross_brl = _consume_base(book.bids, normalized)
    except ValueError as exc:
        return DryRunResult("sell", False, payload, str(exc), None, None, None)
    if gross_brl < rules["min-cost"] or gross_brl > rules["max-cost"]:
        return DryRunResult("sell", False, payload, "valor estimado fora dos limites do par", price, gross_brl, None)
    net_brl = gross_brl * (Decimal("1") - fees.taker_fee)
    return DryRunResult(
        "sell", True, payload, "candidato valido; nenhuma ordem enviada", price, gross_brl, net_brl
    )
