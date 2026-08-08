import argparse
import subprocess
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from sqlalchemy.engine import make_url

from backend.core import database
from backend.main import run_trading_cycle
from backend.tests.preflight_data_date import run_preflight


def run_startup_preflight() -> int:
    return run_preflight(
        asset="BTC/BRL",
        timeframe="1m",
        require_news_today=True,
        max_kline_age_seconds=300,
        require_workers=True,
        require_clock_sync=True,
        max_clock_skew_seconds=300,
    )


def backup_db() -> Path | None:
    """Create a PostgreSQL custom-format dump through the Compose database service."""
    try:
        backups_dir = BACKEND_DIR / "backups"
        backups_dir.mkdir(exist_ok=True)
        timestamp_str = time.strftime("%Y%m%d_%H%M")
        dest_path = backups_dir / f"trading_v2_{timestamp_str}.dump"
        url = make_url(database.DATABASE_URL)
        command = [
            "docker", "compose", "exec", "-T", "db", "pg_dump",
            "--format=custom", "--no-owner", "--no-privileges",
            "--username", url.username or "tgr01",
            "--dbname", url.database or "tgr01",
        ]
        result = subprocess.run(
            command,
            cwd=PROJECT_DIR,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"pg_dump retornou {result.returncode}")
        if not result.stdout:
            raise RuntimeError("pg_dump retornou um arquivo vazio")
        dest_path.write_bytes(result.stdout)
        print(f"[BACKUP] Dump PostgreSQL salvo em: {dest_path.name}")
        return dest_path
    except Exception as e:
        print(f"[WARNING] Falha ao realizar backup do banco: {type(e).__name__}: {e}")
        return None


def start_paper_trading(cycles: int = 288, sleep_seconds: int = 900, backup: bool = True):
    """Run the decision-cycle orchestrator loop.

    sleep_seconds paces how often the Decision Agent / Risk Manager are invoked, not how
    often market data is ingested. price_worker.py polls Mercado Bitcoin independently
    (every 30s) to keep 1-minute klines gapless for technical indicators (RSI/MACD/
    Bollinger/EMA/Volume Profile); it must never be throttled to match this value.
    Default here is 15 minutes (900s) to match the real BTC/BRL candle refresh cadence on
    Mercado Bitcoin -- calling the LLM more often than the underlying price actually moves
    just repeats the same technical snapshot at extra cost with no new information, the same
    class of waste as re-reading stale news. Default cycles (288) preserves the previous
    ~3-day default total runtime (288 * 900s = 72h) that 4320 cycles * 60s used to cover.
    """
    print("=" * 60)
    print("INICIANDO PAPER TRADING ORCHESTRATOR")
    print(f"Meta: {cycles} ciclos simulados rodando em cima de DADOS REAIS.")
    print(f"Intervalo: {sleep_seconds}s entre ciclos.")
    print(f"Database: {database.get_database_label()}")
    print("=" * 60)

    last_backup_time = time.time()

    completed_cycles = 0

    for i in range(cycles):
        print(f"\n--- PAPER TRADING CICLO {i + 1}/{cycles} ---")
        try:
            cycle_completed = run_trading_cycle()
        except Exception as e:
            print(f"[FATAL] Falha no orquestrador: {type(e).__name__}: {e}")
            print("Tentando sobreviver para o proximo ciclo...")
            cycle_completed = False

        if cycle_completed is False:
            print("[BLOQUEADO] Ciclo abortou em preflight/safe mode. Encerrando paper trading para evitar repeticao com dados ruins.")
            print("Sugestao: reinicie os workers, aguarde 30-60s e rode o preflight antes de tentar novamente.")
            break

        completed_cycles += 1

        if backup and time.time() - last_backup_time >= 21600:
            backup_db()
            last_backup_time = time.time()

        if i < cycles - 1 and sleep_seconds > 0:
            print(f"Aguardando {sleep_seconds} segundos para o proximo ciclo de mercado...")
            time.sleep(sleep_seconds)

    if completed_cycles == cycles:
        print("\n>>> PAPER TRADING COMPLETADO COM SUCESSO! <<<")
    else:
        print(f"\n>>> PAPER TRADING INTERROMPIDO: {completed_cycles}/{cycles} ciclos completos. <<<")


def parse_args():
    parser = argparse.ArgumentParser(description="Run paper trading cycles against the configured PostgreSQL database.")
    parser.add_argument("--cycles", type=int, default=288, help="Number of cycles to run. Default: 288 (~3 days at the default 15-minute interval)")
    parser.add_argument("--sleep", type=int, default=900, help="Seconds between decision cycles. Default: 900 (15 minutes, matching real BTC/BRL candle refresh cadence -- independent of price_worker's own ingestion interval)")
    parser.add_argument("--no-backup", action="store_true", help="Skip the startup/periodic DB backup.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    preflight_code = run_startup_preflight()
    if preflight_code:
        raise SystemExit(preflight_code)
    if not args.no_backup:
        backup_db()
    start_paper_trading(cycles=args.cycles, sleep_seconds=args.sleep, backup=not args.no_backup)
