import argparse
import math
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.agents.decision_agent import load_api_keys
from sqlalchemy import func, select

from backend.core import database
from backend.core.database import init_db
from backend.core.db_models import (
    klines,
    news,
    paper_position_reconciliations,
    paper_position_state,
    system_health,
    trade_logs,
    virtual_portfolio,
)
from backend.features.payload_builder import build_agent_payload
from backend.execution.paper_simulator import PaperExecutionConfig
from backend.core.runtime_safety import REQUIRED_WORKERS, assess_worker_heartbeats
from backend.tests.analyze_trade_logs import classify_reason


def local_dt(timestamp: int | None) -> str:
    if timestamp is None:
        return "None"
    return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")


def print_section(title: str):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def current_data_report(blockers: list[str], warnings: list[str]):
    print_section("Dados Atuais")
    now = int(time.time())
    print(f"DB: {database.get_database_label()}")
    print(f"Agora local: {local_dt(now)}")

    with database.engine.connect() as conn:
        latest_kline = conn.execute(
            select(klines.c.timestamp, klines.c.close)
            .where(klines.c.asset == "BTC/BRL", klines.c.timeframe == "1m")
            .order_by(klines.c.timestamp.desc())
            .limit(1)
        ).mappings().first()
    if latest_kline is None:
        blockers.append("Sem candle BTC/BRL 1m no PostgreSQL.")
        print("[FAIL] Sem candle BTC/BRL 1m.")
    else:
        age = now - int(latest_kline["timestamp"])
        print(f"Kline: {local_dt(latest_kline['timestamp'])} age={age}s close={latest_kline['close']}")
        if age > 300:
            blockers.append(f"Candle stale: {age}s > 300s.")
        elif age > 120:
            warnings.append(f"Candle fresco, mas acima de 120s: {age}s.")

    with database.engine.connect() as conn:
        latest_news = conn.execute(
            select(news.c.timestamp, news.c.source, news.c.headline)
            .order_by(news.c.timestamp.desc())
            .limit(1)
        ).mappings().first()
    if latest_news is None:
        warnings.append("Sem noticias no PostgreSQL.")
        print("[WARN] Sem noticias.")
    else:
        age = now - int(latest_news["timestamp"])
        print(f"News: {local_dt(latest_news['timestamp'])} age={age}s source={latest_news['source']}")
        print(f"      {latest_news['headline'][:100]}")
        if age > 21600:
            warnings.append(f"Noticias stale: {age}s > 21600s.")


def worker_report(blockers: list[str], warnings: list[str]):
    print_section("Workers")
    now = int(time.time())
    with database.engine.connect() as conn:
        workers = conn.execute(
            select(system_health.c.worker_name, system_health.c.last_heartbeat).order_by(system_health.c.worker_name)
        ).mappings().all()
    assessment = assess_worker_heartbeats(workers, now=now)
    worker_map = {row["worker_name"]: row["last_heartbeat"] for row in workers}
    for worker, age in assessment.ages_seconds.items():
        print(f"{worker}: heartbeat={local_dt(worker_map[worker])} age={age}s")
        if age > REQUIRED_WORKERS[worker] / 2:
            warnings.append(f"{worker} heartbeat acima de metade do limite: {age}s.")
    blockers.extend(assessment.failures)
    for failure in assessment.failures:
        print(f"[FAIL] {failure}")


def payload_report(blockers: list[str], warnings: list[str]):
    print_section("Payload / Sinais")
    payload = build_agent_payload()
    if payload.get("status") == "ERROR":
        blockers.append(payload.get("message", "Payload retornou ERROR."))
        print(payload)
        return None

    tech = payload["technical_context"]
    health = payload.get("data_health", {})
    news_risk = payload.get("news_risk", {})
    portfolio = payload.get("portfolio_context", {})

    print(f"Preco: {tech.get('current_price')}")
    print(f"RSI: {tech.get('rsi')}")
    print(f"MACD: {tech.get('macd')}")
    print(f"ATR: {tech.get('volatility_atr')}")
    print(f"Data health: market_stale={health.get('is_market_data_stale')} news_stale={health.get('is_news_stale')}")
    print(f"News risk: {news_risk}")
    print(f"Portfolio: {portfolio}")

    if health.get("is_market_data_stale"):
        blockers.append("Payload marcou market_data_stale=True.")
    if health.get("is_news_stale"):
        warnings.append("Payload marcou news_stale=True.")
    if news_risk.get("has_negative_red_flag"):
        warnings.append(f"News red flag: {news_risk.get('matched_terms')}")

    return payload


def capital_state_report(blockers: list[str]):
    print_section("Consistencia de Capital Paper")
    with database.engine.connect() as conn:
        portfolio_rows = conn.execute(
            select(virtual_portfolio.c.currency, virtual_portfolio.c.amount)
        ).mappings()
        amounts = {row["currency"]: float(row["amount"]) for row in portfolio_rows}
        position = conn.execute(
            select(
                paper_position_state.c.quantity,
                paper_position_state.c.avg_cost_brl,
                paper_position_state.c.realized_pnl_brl,
            ).where(paper_position_state.c.asset == "BTC/BRL")
        ).mappings().first()
        reconciliation = conn.execute(
            select(
                paper_position_reconciliations.c.id,
                paper_position_reconciliations.c.timestamp,
                paper_position_reconciliations.c.method,
                paper_position_reconciliations.c.source_log_ids_json,
            )
            .where(paper_position_reconciliations.c.asset == "BTC/BRL")
            .order_by(
                paper_position_reconciliations.c.timestamp.desc(),
                paper_position_reconciliations.c.id.desc(),
            )
            .limit(1)
        ).mappings().first()

    missing = {"BRL", "BTC"} - set(amounts)
    if missing:
        message = f"Portfolio incompleto: moedas ausentes: {', '.join(sorted(missing))}."
        blockers.append(message)
        print(f"[FAIL] {message}")
        return

    brl_balance = amounts["BRL"]
    btc_balance = amounts["BTC"]
    print(f"BRL={brl_balance:.8f} | BTC={btc_balance:.12f}")

    if not all(math.isfinite(value) and value >= 0 for value in (brl_balance, btc_balance)):
        message = "Portfolio possui saldo nao finito ou negativo."
        blockers.append(message)
        print(f"[FAIL] {message}")
        return

    if position is None:
        if btc_balance > 0:
            message = (
                "Saldo BTC existente sem paper_position_state; custo medio legado "
                "precisa de reconciliacao explicita."
            )
            blockers.append(message)
            print(f"[FAIL] {message}")
        else:
            print("[OK] Sem posicao BTC aberta; estado sera criado na primeira ordem paper.")
        return

    quantity = float(position["quantity"])
    avg_cost = float(position["avg_cost_brl"])
    realized_pnl = float(position["realized_pnl_brl"])
    print(f"Position quantity={quantity:.12f} avg_cost={avg_cost:.8f} realized_pnl={realized_pnl:.8f}")
    if reconciliation is not None:
        print(
            "Reconciliation "
            f"id={reconciliation['id']} method={reconciliation['method']} "
            f"timestamp={local_dt(reconciliation['timestamp'])} "
            f"source_logs={reconciliation['source_log_ids_json']}"
        )
    if not all(math.isfinite(value) for value in (quantity, avg_cost, realized_pnl)):
        blockers.append("paper_position_state possui valor nao finito.")
    elif quantity < 0 or avg_cost < 0:
        blockers.append("paper_position_state possui quantidade ou custo negativo.")
    elif not math.isclose(quantity, btc_balance, rel_tol=1e-6, abs_tol=1e-9):
        blockers.append(
            f"Posicao BTC ({quantity:.12f}) diverge do portfolio ({btc_balance:.12f})."
        )


def llm_report(blockers: list[str]):
    print_section("LLM")
    keys = load_api_keys()
    print(f"Chaves configuradas: {len(keys)}")
    if not keys:
        blockers.append("Nenhuma chave LLM configurada; runtime falha para HOLD.")


def execution_config_report(blockers: list[str]):
    print_section("Custos de Execucao Paper")
    try:
        config = PaperExecutionConfig.from_env()
    except ValueError as error:
        blockers.append(f"Configuracao de execucao paper invalida: {error}")
        print(f"[FAIL] {error}")
        return
    print(f"fee_rate={config.fee_rate:.6f} ({config.fee_rate * 100:.3f}%)")
    print(
        "slippage_rate="
        f"{config.min_slippage_rate:.6f}..{config.max_slippage_rate:.6f} "
        f"({config.min_slippage_rate * 100:.3f}%..{config.max_slippage_rate * 100:.3f}%)"
    )
    print(f"atr_slippage_factor={config.atr_slippage_factor:.6f}")


def audit_report(since_id: int | None, warnings: list[str]):
    print_section("Auditoria / Paper Trading")
    with database.engine.connect() as conn:
        scoped = trade_logs.c.id >= since_id if since_id is not None else None
        count_stmt = select(func.count()).select_from(trade_logs)
        if scoped is not None:
            count_stmt = count_stmt.where(scoped)
        total = int(conn.scalar(count_stmt) or 0)
        print(f"Logs analisados: {total}" + (f" desde id {since_id}" if since_id is not None else ""))

        action_stmt = select(trade_logs.c.action, func.count().label("count")).group_by(trade_logs.c.action)
        if scoped is not None:
            action_stmt = action_stmt.where(scoped)
        for row in conn.execute(action_stmt.order_by(func.count().desc())).mappings():
            print(f"Action {row['action']}: {row['count']}")

        reason_stmt = select(trade_logs.c.reasoning, trade_logs.c.llm_reasoning)
        if scoped is not None:
            reason_stmt = reason_stmt.where(scoped)
        buckets = {}
        for row in conn.execute(reason_stmt).mappings():
            bucket = classify_reason(row["reasoning"], row["llm_reasoning"])
            buckets[bucket] = buckets.get(bucket, 0) + 1
        print(f"Buckets: {dict(sorted(buckets.items()))}")

        if buckets.get("llm_technical_failure", 0) > 0:
            warnings.append(f"Falhas tecnicas LLM no periodo: {buckets['llm_technical_failure']}.")
        if buckets.get("stale_data", 0) > 0:
            warnings.append(f"Stale data auditado no periodo: {buckets['stale_data']}.")

        print("Ultimos 5 logs:")
        latest_stmt = select(
            trade_logs.c.id,
            trade_logs.c.timestamp,
            trade_logs.c.llm_action,
            trade_logs.c.llm_reasoning,
            trade_logs.c.action,
            trade_logs.c.llm_conviction,
            trade_logs.c.reasoning,
        ).order_by(trade_logs.c.id.desc()).limit(5)
        for row in conn.execute(latest_stmt).mappings():
            print(dict(row))

        portfolio_rows = conn.execute(
            select(virtual_portfolio.c.currency, virtual_portfolio.c.amount).order_by(virtual_portfolio.c.currency)
        ).mappings()
        amounts = {row["currency"]: float(row["amount"]) for row in portfolio_rows}
        price_row = conn.execute(
            select(klines.c.close)
            .where(klines.c.asset == "BTC/BRL", klines.c.timeframe == "1m")
            .order_by(klines.c.timestamp.desc())
            .limit(1)
        ).mappings().first()
        if price_row:
            latest_price = float(price_row["close"])
            total_equity = amounts.get("BRL", 0.0) + amounts.get("BTC", 0.0) * latest_price
            exposure = (amounts.get("BTC", 0.0) * latest_price / total_equity * 100.0) if total_equity else 0.0
            print(f"Equity: {total_equity:.2f} BRL | exposure={exposure:.2f}% | latest_price={latest_price:.2f}")


def final_verdict(blockers: list[str], warnings: list[str], strict: bool):
    print_section("Veredito")
    if blockers:
        print("BLOCKED")
        for item in blockers:
            print(f"[BLOCKER] {item}")
        return 1

    if warnings:
        print("PASS_WITH_WARNINGS")
        for item in warnings:
            print(f"[WARN] {item}")
        return 1 if strict else 0

    print("PASS")
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Generate an operational trading readiness report.")
    parser.add_argument("--since-id", type=int, default=None, help="Only evaluate trade logs with id >= this value.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when warnings exist.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    init_db()
    blockers: list[str] = []
    warnings: list[str] = []

    current_data_report(blockers, warnings)
    worker_report(blockers, warnings)
    payload_report(blockers, warnings)
    capital_state_report(blockers)
    execution_config_report(blockers)
    llm_report(blockers)
    audit_report(args.since_id, warnings)
    raise SystemExit(final_verdict(blockers, warnings, strict=args.strict))
