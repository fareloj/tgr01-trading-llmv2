from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable


LEGACY_INITIAL_BRL = 10000.0
LEGACY_INITIAL_BTC = 0.0
LEGACY_FEE_RATE = 0.003
BALANCE_ABS_TOLERANCE = 1e-9


@dataclass(frozen=True)
class LegacyExecutionStep:
    log_id: int
    action: str
    executed_size_pct: float
    price: float
    equity_before_brl: float
    gross_notional_brl: float
    fee_brl: float
    brl_delta: float
    btc_delta: float
    brl_after: float
    btc_after: float
    avg_cost_brl: float
    realized_pnl_brl: float


@dataclass(frozen=True)
class LegacyReconstruction:
    initial_brl: float
    initial_btc: float
    final_brl: float
    final_btc: float
    avg_cost_brl: float
    realized_pnl_brl: float
    steps: tuple[LegacyExecutionStep, ...]

    @property
    def source_log_ids(self) -> list[int]:
        return [step.log_id for step in self.steps]

    def details_json(self) -> str:
        return json.dumps([asdict(step) for step in self.steps], ensure_ascii=False, sort_keys=True)


def _finite_nonnegative(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} deve ser finito e nao negativo.")
    return number


def reconstruct_legacy_orders(
    rows: Iterable[dict[str, Any]],
    *,
    initial_brl: float = LEGACY_INITIAL_BRL,
    initial_btc: float = LEGACY_INITIAL_BTC,
    fee_rate: float = LEGACY_FEE_RATE,
) -> LegacyReconstruction:
    brl = _finite_nonnegative(initial_brl, "initial_brl")
    btc = _finite_nonnegative(initial_btc, "initial_btc")
    fee_rate = _finite_nonnegative(fee_rate, "fee_rate")
    if fee_rate >= 1:
        raise ValueError("fee_rate deve ser menor que 1.")

    avg_cost = 0.0
    realized_pnl = 0.0
    steps: list[LegacyExecutionStep] = []
    for raw in rows:
        log_id = int(raw["id"])
        action = str(raw["action"]).upper()
        if action not in {"BUY", "SELL"}:
            raise ValueError(f"Log {log_id}: acao legada invalida: {action!r}.")
        if any(raw.get(field) is not None for field in ("brl_delta", "btc_delta", "fee_brl")):
            raise ValueError(f"Log {log_id}: mistura auditoria moderna com replay legado.")

        size_pct = _finite_nonnegative(raw.get("executed_size"), f"log {log_id} executed_size")
        price = _finite_nonnegative(raw.get("execution_price"), f"log {log_id} execution_price")
        if price <= 0 or size_pct > 100:
            raise ValueError(f"Log {log_id}: preco ou sizing fora do dominio legado.")

        equity_before = brl + btc * price
        target_notional = equity_before * (size_pct / 100.0)
        if action == "BUY":
            gross = min(target_notional, brl)
            fee = gross * fee_rate
            btc_delta = (gross - fee) / price
            brl_delta = -gross
            new_btc = btc + btc_delta
            avg_cost = ((btc * avg_cost) + gross) / new_btc if new_btc > 0 else 0.0
        else:
            target_btc = target_notional / price
            sold = min(target_btc, btc)
            gross = sold * price
            fee = gross * fee_rate
            btc_delta = -sold
            brl_delta = gross - fee
            realized_pnl += brl_delta - sold * avg_cost
            new_btc = max(0.0, btc + btc_delta)
            if new_btc == 0:
                avg_cost = 0.0

        brl += brl_delta
        btc = max(0.0, btc + btc_delta)
        steps.append(
            LegacyExecutionStep(
                log_id=log_id,
                action=action,
                executed_size_pct=size_pct,
                price=price,
                equity_before_brl=equity_before,
                gross_notional_brl=gross,
                fee_brl=fee,
                brl_delta=brl_delta,
                btc_delta=btc_delta,
                brl_after=brl,
                btc_after=btc,
                avg_cost_brl=avg_cost,
                realized_pnl_brl=realized_pnl,
            )
        )

    if not steps:
        raise ValueError("Nenhuma ordem BUY/SELL legada encontrada para reconstruir.")
    return LegacyReconstruction(
        initial_brl=float(initial_brl),
        initial_btc=float(initial_btc),
        final_brl=brl,
        final_btc=btc,
        avg_cost_brl=avg_cost,
        realized_pnl_brl=realized_pnl,
        steps=tuple(steps),
    )


def assert_reconstruction_matches_portfolio(
    reconstruction: LegacyReconstruction,
    observed_portfolio: dict[str, float],
) -> None:
    missing = {"BRL", "BTC"} - set(observed_portfolio)
    if missing:
        raise RuntimeError(f"Portfolio incompleto: moedas ausentes: {', '.join(sorted(missing))}")
    observed_brl = _finite_nonnegative(observed_portfolio["BRL"], "observed BRL")
    observed_btc = _finite_nonnegative(observed_portfolio["BTC"], "observed BTC")
    if not math.isclose(reconstruction.final_brl, observed_brl, rel_tol=0.0, abs_tol=BALANCE_ABS_TOLERANCE):
        raise RuntimeError(
            f"Replay legado nao fecha BRL: reconstruido={reconstruction.final_brl:.12f} "
            f"observado={observed_brl:.12f}."
        )
    if not math.isclose(reconstruction.final_btc, observed_btc, rel_tol=0.0, abs_tol=BALANCE_ABS_TOLERANCE):
        raise RuntimeError(
            f"Replay legado nao fecha BTC: reconstruido={reconstruction.final_btc:.15f} "
            f"observado={observed_btc:.15f}."
        )
