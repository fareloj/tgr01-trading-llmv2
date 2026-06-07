import time
from dataclasses import dataclass
from sqlite3 import Cursor


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
    cursor: Cursor,
    action: str,
    executed_size_pct: float,
    current_price: float,
    payload: dict,
    config: PaperExecutionConfig = PaperExecutionConfig(),
) -> dict:
    action = action.upper()
    if action not in {"BUY", "SELL"} or executed_size_pct <= 0 or current_price <= 0:
        return empty_execution_audit(current_price)

    brl_balance, btc_balance = _portfolio_balances(cursor)
    equity_before = brl_balance + (btc_balance * current_price)
    if equity_before <= 0:
        return empty_execution_audit(current_price)

    slippage_rate = estimate_slippage_rate(payload, config)
    expected_price = current_price
    effective_price = current_price * (1.0 + slippage_rate) if action == "BUY" else current_price * (1.0 - slippage_rate)
    target_notional = equity_before * (executed_size_pct / 100.0)
    position = _ensure_position_state(cursor, btc_balance, current_price)

    if action == "BUY":
        result = _execute_buy(
            cursor=cursor,
            brl_balance=brl_balance,
            btc_balance=btc_balance,
            position=position,
            target_notional=target_notional,
            effective_price=effective_price,
            fee_rate=config.fee_rate,
        )
    else:
        result = _execute_sell(
            cursor=cursor,
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


def _portfolio_balances(cursor: Cursor) -> tuple[float, float]:
    cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BRL'")
    brl_row = cursor.fetchone()
    cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BTC'")
    btc_row = cursor.fetchone()
    return (
        float(brl_row["amount"] if brl_row else 0.0),
        float(btc_row["amount"] if btc_row else 0.0),
    )


def _ensure_position_state(cursor: Cursor, btc_balance: float, current_price: float) -> dict:
    cursor.execute(
        """
        SELECT asset, quantity, avg_cost_brl, realized_pnl_brl
        FROM paper_position_state
        WHERE asset = 'BTC/BRL'
        """
    )
    row = cursor.fetchone()
    if row:
        return {
            "quantity": float(row["quantity"]),
            "avg_cost_brl": float(row["avg_cost_brl"]),
            "realized_pnl_brl": float(row["realized_pnl_brl"]),
        }

    avg_cost = current_price if btc_balance > 0 else 0.0
    cursor.execute(
        """
        INSERT INTO paper_position_state (asset, quantity, avg_cost_brl, realized_pnl_brl, updated_at)
        VALUES ('BTC/BRL', ?, ?, 0.0, ?)
        """,
        (btc_balance, avg_cost, int(time.time())),
    )
    return {"quantity": btc_balance, "avg_cost_brl": avg_cost, "realized_pnl_brl": 0.0}


def _execute_buy(
    cursor: Cursor,
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

    cursor.execute("UPDATE virtual_portfolio SET amount = amount + ? WHERE currency='BRL'", (brl_delta,))
    cursor.execute("UPDATE virtual_portfolio SET amount = amount + ? WHERE currency='BTC'", (btc_delta,))

    old_quantity = max(float(position["quantity"]), btc_balance)
    old_avg = float(position["avg_cost_brl"])
    new_quantity = old_quantity + btc_delta
    new_avg = ((old_quantity * old_avg) + gross_notional) / new_quantity if new_quantity > 0 else 0.0
    _update_position(cursor, new_quantity, new_avg, float(position["realized_pnl_brl"]))

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
    cursor: Cursor,
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

    cursor.execute("UPDATE virtual_portfolio SET amount = amount + ? WHERE currency='BRL'", (brl_delta,))
    cursor.execute("UPDATE virtual_portfolio SET amount = amount + ? WHERE currency='BTC'", (btc_delta,))

    avg_cost = float(position["avg_cost_brl"])
    realized_pnl = net_notional - (btc_sold * avg_cost)
    total_realized = float(position["realized_pnl_brl"]) + realized_pnl
    new_quantity = max(0.0, max(float(position["quantity"]), btc_balance) - btc_sold)
    new_avg = avg_cost if new_quantity > 0 else 0.0
    _update_position(cursor, new_quantity, new_avg, total_realized)

    return {
        "fee_brl": fee_brl,
        "gross_notional_brl": gross_notional,
        "net_notional_brl": net_notional,
        "brl_delta": brl_delta,
        "btc_delta": btc_delta,
        "realized_pnl_brl": realized_pnl,
        "position_avg_cost_brl": new_avg,
    }


def _update_position(cursor: Cursor, quantity: float, avg_cost_brl: float, realized_pnl_brl: float) -> None:
    cursor.execute(
        """
        UPDATE paper_position_state
        SET quantity = ?, avg_cost_brl = ?, realized_pnl_brl = ?, updated_at = ?
        WHERE asset = 'BTC/BRL'
        """,
        (quantity, avg_cost_brl, realized_pnl_brl, int(time.time())),
    )
