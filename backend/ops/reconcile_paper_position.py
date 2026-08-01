from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

from sqlalchemy import select


PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database, repository
from backend.core.db_models import klines


def reconcile_position(connection, *, avg_cost_brl: float, force: bool = False) -> dict:
    if not math.isfinite(avg_cost_brl) or avg_cost_brl <= 0:
        raise ValueError("avg_cost_brl deve ser finito e maior que zero.")

    portfolio = repository.get_virtual_portfolio(connection=connection, for_update=True)
    missing = {"BRL", "BTC"} - set(portfolio)
    if missing:
        raise RuntimeError(f"Portfolio incompleto: moedas ausentes: {', '.join(sorted(missing))}")

    btc_balance = float(portfolio["BTC"])
    if not math.isfinite(btc_balance) or btc_balance <= 0:
        raise RuntimeError("Nao existe saldo BTC positivo para reconciliar.")

    current = repository.get_paper_position_state("BTC/BRL", connection=connection, for_update=True)
    if current is not None and not force:
        raise RuntimeError("paper_position_state ja existe; use --force somente apos auditoria manual.")

    realized_pnl = float(current["realized_pnl_brl"]) if current else 0.0
    repository.update_paper_position_state(
        "BTC/BRL",
        btc_balance,
        avg_cost_brl,
        realized_pnl,
        int(time.time()),
        connection=connection,
    )
    return {
        "asset": "BTC/BRL",
        "quantity": btc_balance,
        "avg_cost_brl": avg_cost_brl,
        "realized_pnl_brl": realized_pnl,
    }


def latest_price(connection) -> float:
    value = connection.scalar(
        select(klines.c.close)
        .where(klines.c.asset == "BTC/BRL", klines.c.timeframe == "1m")
        .order_by(klines.c.timestamp.desc())
        .limit(1)
    )
    price = float(value or 0.0)
    if not math.isfinite(price) or price <= 0:
        raise RuntimeError("Nao ha preco BTC/BRL valido para marcacao a mercado.")
    return price


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile legacy paper BTC cost basis without changing portfolio balances."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--avg-cost-brl", type=float, help="Custo medio BRL auditado pelo operador.")
    source.add_argument(
        "--mark-to-market",
        action="store_true",
        help="Usa o ultimo candle como novo baseline paper, nao como custo historico real.",
    )
    parser.add_argument("--confirm", action="store_true", help="Persiste a reconciliacao exibida no preview.")
    parser.add_argument("--force", action="store_true", help="Substitui estado existente apos auditoria manual.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database.init_db()
    with database.engine.connect() as connection:
        portfolio = repository.get_virtual_portfolio(connection=connection)
        position = repository.get_paper_position_state("BTC/BRL", connection=connection)
        print(f"DB: {database.get_database_label()}")
        print(f"Portfolio: {portfolio}")
        print(f"Position: {position}")

        if args.avg_cost_brl is None and not args.mark_to_market:
            print("[PREVIEW] Nenhuma alteracao solicitada.")
            return 0

        chosen_cost = latest_price(connection) if args.mark_to_market else float(args.avg_cost_brl)
        source = "mark-to-market" if args.mark_to_market else "custo medio informado"
        print(f"[PREVIEW] source={source} avg_cost_brl={chosen_cost:.8f}")

    if not args.confirm:
        print("[PREVIEW] Nada foi persistido. Revise e repita com --confirm.")
        return 0

    with database.engine.begin() as connection:
        reconciled = reconcile_position(connection, avg_cost_brl=chosen_cost, force=args.force)
    print(f"[OK] Estado paper reconciliado: {reconciled}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
