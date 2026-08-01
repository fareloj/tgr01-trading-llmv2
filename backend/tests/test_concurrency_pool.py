"""
Teste de concorrencia do pool (item 4 do TODO / a razao da migracao SQLite -> PostgreSQL).

Prova que escritas concorrentes atraves do QueuePool:
  - nao dao 'database is locked';
  - nao estouram o pool;
  - nao perdem writes.

Roda contra o PostgreSQL configurado no conftest. Em SQLite, o segundo teste
(incrementos na MESMA linha) tipicamente falharia/locaria — que e justamente o
problema que a migracao existe para resolver.
"""
import threading
import pytest
from sqlalchemy import select, func
from backend.core import database, repository
from backend.core.db_models import klines


def _run_threads(target, n_threads):
    """Roda `target(idx)` em n_threads com largada simultanea (Barrier) para
    maximizar contencao. Coleta qualquer excecao levantada nas threads."""
    errors = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def wrapped(idx):
        try:
            barrier.wait()
            target(idx)
        except Exception as e:  # noqa: BLE001
            with lock:
                errors.append(repr(e))

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_concurrent_distinct_inserts_no_locks_no_loss():
    """N threads inserindo klines distintas ao mesmo tempo: zero erro, zero perda."""
    n_threads = 20
    per_thread = 25
    base_ts = 1_700_000_000

    def worker(idx):
        for i in range(per_thread):
            repository.upsert_kline({
                "asset": "BTC/BRL",
                "timeframe": "1m",
                "timestamp": base_ts + idx * per_thread + i,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1.0,
            })

    errors = _run_threads(worker, n_threads)
    assert errors == [], f"Erros sob concorrencia: {errors}"

    with database.engine.connect() as conn:
        total = conn.scalar(select(func.count()).select_from(klines))
    assert total == n_threads * per_thread, (
        f"Writes perdidos: {total} != {n_threads * per_thread}"
    )


def test_concurrent_same_row_increments_no_lost_updates():
    """N threads incrementando a MESMA linha (BRL) ao mesmo tempo.

    Prova que o incremento atomico no nivel do banco (amount = amount + delta)
    serializa sob contencao sem perder nenhum update — o cenario que o SQLite
    nao aguentava."""
    n_threads = 20
    increments = 25
    delta = 1.0

    def worker(_idx):
        for _ in range(increments):
            repository.update_virtual_portfolio_delta("BRL", delta)

    errors = _run_threads(worker, n_threads)
    assert errors == [], f"Erros sob concorrencia: {errors}"

    portfolio = repository.get_virtual_portfolio()
    expected = 10000.0 + (n_threads * increments * delta)  # seed do conftest = 10000 BRL
    assert portfolio["BRL"] == pytest.approx(expected), (
        f"Updates perdidos: BRL={portfolio['BRL']} != {expected}"
    )
