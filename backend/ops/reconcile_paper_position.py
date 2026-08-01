from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

from sqlalchemy import select


PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import database, repository
from backend.core.db_models import klines, trade_logs
from backend.execution.legacy_reconciliation import (
    assert_reconstruction_matches_portfolio,
    reconstruct_legacy_orders,
)


def reconcile_position(
    connection,
    *,
    avg_cost_brl: float,
    force: bool = False,
    method: str = "manual_explicit",
    initial_brl: float | None = None,
    initial_btc: float | None = None,
    reconstructed_brl: float | None = None,
    reconstructed_btc: float | None = None,
    realized_pnl_brl: float | None = None,
    source_log_ids: list[int] | None = None,
    details_json: str = "{}",
) -> dict:
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

    realized_pnl = (
        float(realized_pnl_brl)
        if realized_pnl_brl is not None
        else (float(current["realized_pnl_brl"]) if current else 0.0)
    )
    if not math.isfinite(realized_pnl):
        raise ValueError("realized_pnl_brl deve ser finito.")
    observed_brl = float(portfolio["BRL"])
    repository.update_paper_position_state(
        "BTC/BRL",
        btc_balance,
        avg_cost_brl,
        realized_pnl,
        int(time.time()),
        connection=connection,
    )
    reconciliation_id = repository.add_paper_position_reconciliation(
        {
            "asset": "BTC/BRL",
            "timestamp": int(time.time()),
            "method": method,
            "initial_brl": observed_brl if initial_brl is None else float(initial_brl),
            "initial_btc": btc_balance if initial_btc is None else float(initial_btc),
            "reconstructed_brl": observed_brl if reconstructed_brl is None else float(reconstructed_brl),
            "reconstructed_btc": btc_balance if reconstructed_btc is None else float(reconstructed_btc),
            "observed_brl": observed_brl,
            "observed_btc": btc_balance,
            "avg_cost_brl": avg_cost_brl,
            "realized_pnl_brl": realized_pnl,
            "source_log_ids_json": json.dumps(source_log_ids or []),
            "details_json": details_json,
        },
        connection=connection,
    )
    return {
        "asset": "BTC/BRL",
        "quantity": btc_balance,
        "avg_cost_brl": avg_cost_brl,
        "realized_pnl_brl": realized_pnl,
        "reconciliation_id": reconciliation_id,
        "method": method,
    }


def reconstruct_from_database(connection):
    rows = connection.execute(
        select(
            trade_logs.c.id,
            trade_logs.c.action,
            trade_logs.c.executed_size,
            trade_logs.c.execution_price,
            trade_logs.c.fee_brl,
            trade_logs.c.brl_delta,
            trade_logs.c.btc_delta,
        )
        .where(trade_logs.c.action.in_(["BUY", "SELL"]))
        .order_by(trade_logs.c.id)
    ).mappings()
    reconstruction = reconstruct_legacy_orders([dict(row) for row in rows])
    portfolio = repository.get_virtual_portfolio(connection=connection)
    assert_reconstruction_matches_portfolio(reconstruction, portfolio)
    return reconstruction


def reconcile_from_legacy_logs(connection) -> dict:
    portfolio = repository.get_virtual_portfolio(connection=connection, for_update=True)
    current = repository.get_paper_position_state("BTC/BRL", connection=connection, for_update=True)
    if current is not None:
        raise RuntimeError("paper_position_state ja existe; replay legado nao pode sobrescreve-lo.")
    reconstruction = reconstruct_from_database(connection)
    assert_reconstruction_matches_portfolio(reconstruction, portfolio)
    return reconcile_position(
        connection,
        avg_cost_brl=reconstruction.avg_cost_brl,
        method="legacy_trade_log_replay_v1",
        initial_brl=reconstruction.initial_brl,
        initial_btc=reconstruction.initial_btc,
        reconstructed_brl=reconstruction.final_brl,
        reconstructed_btc=reconstruction.final_btc,
        realized_pnl_brl=reconstruction.realized_pnl_brl,
        source_log_ids=reconstruction.source_log_ids,
        details_json=reconstruction.details_json(),
    )


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
    source.add_argument(
        "--from-legacy-logs",
        action="store_true",
        help="Reconstrui o custo medio apenas se o replay das ordens fechar exatamente com a carteira.",
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

        if args.avg_cost_brl is None and not args.mark_to_market and not args.from_legacy_logs:
            print("[PREVIEW] Nenhuma alteracao solicitada.")
            return 0

        reconstruction = reconstruct_from_database(connection) if args.from_legacy_logs else None
        chosen_cost = (
            reconstruction.avg_cost_brl
            if reconstruction is not None
            else (latest_price(connection) if args.mark_to_market else float(args.avg_cost_brl))
        )
        source = (
            "legacy_trade_log_replay_v1"
            if reconstruction is not None
            else ("mark-to-market" if args.mark_to_market else "custo medio informado")
        )
        print(f"[PREVIEW] source={source} avg_cost_brl={chosen_cost:.8f}")
        if reconstruction is not None:
            print(
                "[PREVIEW] "
                f"logs={reconstruction.source_log_ids} "
                f"final_brl={reconstruction.final_brl:.12f} "
                f"final_btc={reconstruction.final_btc:.15f}"
            )

    if not args.confirm:
        print("[PREVIEW] Nada foi persistido. Revise e repita com --confirm.")
        return 0

    with database.engine.begin() as connection:
        if args.from_legacy_logs:
            reconciled = reconcile_from_legacy_logs(connection)
        else:
            reconciled = reconcile_position(
                connection,
                avg_cost_brl=chosen_cost,
                force=args.force,
                method="mark_to_market" if args.mark_to_market else "manual_explicit",
                details_json=json.dumps({"source": source}, sort_keys=True),
            )
    print(f"[OK] Estado paper reconciliado: {reconciled}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
