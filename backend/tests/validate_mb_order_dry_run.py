"""Authenticated BUY/SELL validation against live MB balances and orderbook."""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / "backend" / ".env")

from backend.execution.mb_order_dry_run import validate_market_buy, validate_market_sell
from backend.execution.mb_private_client import MBCredentials, MBReadOnlyClient


def _money(value: Decimal, places: str) -> str:
    return str(value.quantize(Decimal(places)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate MB BUY/SELL candidates without submitting orders."
    )
    parser.add_argument(
        "--buy-brl", type=Decimal, default=Decimal(os.getenv("MB_DRY_RUN_BUY_BRL", "1.00"))
    )
    parser.add_argument(
        "--sell-btc", type=Decimal, default=Decimal(os.getenv("MB_DRY_RUN_SELL_BTC", "0.00000150"))
    )
    args = parser.parse_args()

    if os.getenv("REAL_TRADING_ENABLED", "false").strip().lower() == "true":
        raise RuntimeError("Dry-run recusado porque REAL_TRADING_ENABLED=true.")

    client = MBReadOnlyClient(MBCredentials.from_env())
    balances = client.list_balances()
    fees = client.get_trading_fees()
    rules = client.get_symbol_rules()
    book = client.get_orderbook()

    buy = validate_market_buy(args.buy_brl, balances, fees, rules, book)
    sell = validate_market_sell(args.sell_btc, balances, fees, rules, book)

    print("Mercado Bitcoin private API: autenticacao e leituras OK")
    print("Modo: DRY-RUN ESTRITO (endpoint POST /orders nao existe neste cliente)")
    print(f"Saldo BRL disponivel: {_money(balances.get('BRL').available if balances.get('BRL') else Decimal(0), '0.01')}")
    print(f"Saldo BTC disponivel: {_money(balances.get('BTC').available if balances.get('BTC') else Decimal(0), '0.00000001')}")
    print(f"Taker fee: {fees.taker_fee}")
    print(f"Book: best_ask={book.asks[0].price} best_bid={book.bids[0].price} timestamp={book.timestamp}")
    for result in (buy, sell):
        status = "VALIDO" if result.valid else "BLOQUEADO"
        print(f"[{status}] {result.side.upper()} payload={result.payload} motivo={result.reason}")
        if result.reference_price is not None:
            print(
                f"  preco medio estimado={result.reference_price} "
                f"bruto={result.estimated_gross} liquido_estimado={result.estimated_net}"
            )
    print("[OK] Validacao concluida. Zero ordens enviadas.")
    # A candidate blocked by balance or exchange limits is a successful safety
    # result. API/schema failures still raise and return a non-zero exit code.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
