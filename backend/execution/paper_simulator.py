import math
import time
from dataclasses import dataclass
from typing import Any

from backend.core import repository


@dataclass(frozen=True)
class PaperExecutionConfig:
    fee_rate: float = 0.003
    min_slippage_rate: float = 0.0005
    max_slippage_rate: float = 0.003
    atr_slippage_factor: float = 0.10


def empty_execution_audit(current_price: float) -> dict:
    return {
        "fee_rate": 0.0,
        "fee_brl": 0.0,
        "slippage_rate": 0.0,
        "expected_price": current_price,
        "effective_price": current_price,
        "gross_notional_brl": 0.0,
        "net_notional_brl": 0.0,
        "brl_delta": 0.0,
        "btc_delta": 0.0,
        "equity_before_brl": None,
        "equity_after_brl": None,
        "realized_pnl_brl": 0.0,
        "position_avg_cost_brl": None,
    }


def estimate_slippage_rate(payload: dict, config: PaperExecutionConfig = PaperExecutionConfig()) -> float:
    tech = payload.get("technical_context", {})
    current_price = float(tech.get("current_price") or 0.0)
    atr = tech.get("volatility_atr", 0.0)
    if isinstance(atr, dict):
        atr = atr.get("value", 0.0)
    atr = float(atr or 0.0)

    if current_price <= 0:
        return config.min_slippage_rate

    volatility_component = (atr / current_price) * config.atr_slippage_factor
    return min(config.max_slippage_rate, max(config.min_slippage_rate, volatility_component))


def execute_paper_order(
    connection: Any,
    action: str,
    executed_size_pct: float,
    current_price: float,
    payload: dict,
    config: PaperExecutionConfig = PaperExecutionConfig(),
) -> dict:
    action = action.upper()
    if not math.isfinite(float(executed_size_pct)) or not math.isfinite(float(current_price)):
        raise ValueError("Paper execution requires finite size and price values.")
    if action not in {"BUY", "SELL"} or executed_size_pct <= 0 or current_price <= 0:
        return empty_execution_audit(current_price)

    brl_balance, btc_balance = _portfolio_balances(connection)
    equity_before = brl_balance + (btc_balance * current_price)
    if equity_before <= 0:
        return empty_execution_audit(current_price)

    slippage_rate = estimate_slippage_rate(payload, config)
    expected_price = current_price
    effective_price = current_price * (1.0 + slippage_rate) if action == "BUY" else current_price * (1.0 - slippage_rate)
    target_notional = equity_before * (executed_size_pct / 100.0)
    position = _ensure_position_state(connection, btc_balance, current_price)
    _assert_position_in_sync(position, btc_balance)

    if action == "BUY":
        result = _execute_buy(
            connection=connection,
            brl_balance=brl_balance,
            btc_balance=btc_balance,
            position=position,
            target_notional=target_notional,
            effective_price=effective_price,
            fee_rate=config.fee_rate,
        )
    else:
        result = _execute_sell(
            connection=connection,
            btc_balance=btc_balance,
            position=position,
            target_notional=target_notional,
            effective_price=effective_price,
            fee_rate=config.fee_rate,
        )

    brl_after = brl_balance + result["brl_delta"]
    btc_after = btc_balance + result["btc_delta"]
    equity_after = brl_after + (btc_after * current_price)

    result.update(
        {
            "fee_rate": config.fee_rate,
            "slippage_rate": slippage_rate,
            "expected_price": expected_price,
            "effective_price": effective_price,
            "equity_before_brl": equity_before,
            "equity_after_brl": equity_after,
        }
    )
    return result


def _portfolio_balances(connection: Any) -> tuple[float, float]:
    portfolio = repository.get_virtual_portfolio(connection=connection, for_update=True)
    missing = {"BRL", "BTC"} - set(portfolio)
    if missing:
        raise RuntimeError(f"Portfolio incompleto: moedas ausentes: {', '.join(sorted(missing))}")

    brl_balance = float(portfolio["BRL"])
    btc_balance = float(portfolio["BTC"])
    if not all(math.isfinite(value) and value >= 0 for value in (brl_balance, btc_balance)):
        raise RuntimeError("Portfolio invalido: saldos BRL/BTC devem ser finitos e nao negativos.")
    return brl_balance, btc_balance


def _ensure_position_state(connection: Any, btc_balance: float, current_price: float) -> dict:
    row = repository.get_paper_position_state("BTC/BRL", connection=connection, for_update=True)
    if row:
        position = {
            "quantity": float(row["quantity"]),
            "avg_cost_brl": float(row["avg_cost_brl"]),
            "realized_pnl_brl": float(row["realized_pnl_brl"]),
        }
        if not all(math.isfinite(value) for value in position.values()):
            raise RuntimeError("Estado da posicao paper contem valor nao finito.")
        if position["quantity"] < 0 or position["avg_cost_brl"] < 0:
            raise RuntimeError("Estado da posicao paper contem quantidade ou custo negativo.")
        return position

    if btc_balance > 0:
        raise RuntimeError(
            "Estado legado nao reconciliado: existe saldo BTC no portfolio, mas "
            "paper_position_state nao possui custo medio. Defina a posicao paper "
            "explicitamente antes de executar nova ordem."
        )

    avg_cost = 0.0
    repository.update_paper_position_state(
        "BTC/BRL",
        btc_balance,
        avg_cost,
        0.0,
        int(time.time()),
        connection=connection
    )
    return {"quantity": btc_balance, "avg_cost_brl": avg_cost, "realized_pnl_brl": 0.0}


def _assert_position_in_sync(position: dict, btc_balance: float) -> None:
    """Fail-loud: em paper trading, a posicao rastreada (paper_position_state) e o
    saldo real de BTC (virtual_portfolio) sao escritos juntos na MESMA transacao e
    NUNCA devem divergir. Se divergirem, ha bug em outro lugar ou saldo externo que
    ainda nao passou por reconciliacao. A execucao deve abortar antes de tocar no
    portfolio; reconciliacao nunca pode ser inferida por max/min silencioso."""
    tracked = float(position["quantity"])
    if not math.isclose(tracked, btc_balance, rel_tol=1e-6, abs_tol=1e-9):
        raise RuntimeError(
            f"Estado de capital inconsistente: posicao rastreada ({tracked:.10f} BTC) "
            f"!= saldo real ({btc_balance:.10f} BTC). Em paper trading isso nunca deveria "
            f"acontecer. Abortando execucao para nao corromper o custo medio."
        )


def _execute_buy(
    connection: Any,
    brl_balance: float,
    btc_balance: float,
    position: dict,
    target_notional: float,
    effective_price: float,
    fee_rate: float,
) -> dict:
    gross_notional = min(target_notional, brl_balance)
    fee_brl = gross_notional * fee_rate
    net_notional = max(0.0, gross_notional - fee_brl)
    btc_delta = net_notional / effective_price if effective_price > 0 else 0.0
    brl_delta = -gross_notional

    repository.update_virtual_portfolio_delta("BRL", brl_delta, connection=connection)
    repository.update_virtual_portfolio_delta("BTC", btc_delta, connection=connection)

    old_quantity = float(position["quantity"])
    old_avg = float(position["avg_cost_brl"])
    new_quantity = old_quantity + btc_delta
    new_avg = ((old_quantity * old_avg) + gross_notional) / new_quantity if new_quantity > 0 else 0.0
    _update_position(connection, new_quantity, new_avg, float(position["realized_pnl_brl"]))

    return {
        "fee_brl": fee_brl,
        "gross_notional_brl": gross_notional,
        "net_notional_brl": net_notional,
        "brl_delta": brl_delta,
        "btc_delta": btc_delta,
        "realized_pnl_brl": 0.0,
        "position_avg_cost_brl": new_avg,
    }


def _execute_sell(
    connection: Any,
    btc_balance: float,
    position: dict,
    target_notional: float,
    effective_price: float,
    fee_rate: float,
) -> dict:
    target_btc = target_notional / effective_price if effective_price > 0 else 0.0
    btc_sold = min(target_btc, btc_balance)
    gross_notional = btc_sold * effective_price
    fee_brl = gross_notional * fee_rate
    net_notional = max(0.0, gross_notional - fee_brl)
    brl_delta = net_notional
    btc_delta = -btc_sold

    repository.update_virtual_portfolio_delta("BRL", brl_delta, connection=connection)
    repository.update_virtual_portfolio_delta("BTC", btc_delta, connection=connection)

    avg_cost = float(position["avg_cost_brl"])
    realized_pnl = net_notional - (btc_sold * avg_cost)
    total_realized = float(position["realized_pnl_brl"]) + realized_pnl
    new_quantity = max(0.0, float(position["quantity"]) - btc_sold)
    new_avg = avg_cost if new_quantity > 0 else 0.0
    _update_position(connection, new_quantity, new_avg, total_realized)

    return {
        "fee_brl": fee_brl,
        "gross_notional_brl": gross_notional,
        "net_notional_brl": net_notional,
        "brl_delta": brl_delta,
        "btc_delta": btc_delta,
        "realized_pnl_brl": realized_pnl,
        "position_avg_cost_brl": new_avg,
    }


def _update_position(connection: Any, quantity: float, avg_cost_brl: float, realized_pnl_brl: float) -> None:
    repository.update_paper_position_state(
        "BTC/BRL",
        quantity,
        avg_cost_brl,
        realized_pnl_brl,
        int(time.time()),
        connection=connection
    )
