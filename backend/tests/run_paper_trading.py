import argparse
import shutil
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from core.database import get_db_path
from main import run_trading_cycle


def backup_db():
    try:
        backups_dir = BACKEND_DIR / "backups"
        backups_dir.mkdir(exist_ok=True)
        db_path = get_db_path()

        timestamp_str = time.strftime("%Y%m%d_%H%M")
        dest_path = backups_dir / f"trading_v2_{timestamp_str}.db"

        if db_path.exists():
            shutil.copy2(db_path, dest_path)
            print(f"[BACKUP] Banco de dados salvo com seguranca em: {dest_path.name}")
    except Exception as e:
        print(f"[WARNING] Falha ao realizar backup do banco: {type(e).__name__}: {e}")


def start_paper_trading(cycles: int = 4320, sleep_seconds: int = 60, backup: bool = True):
    print("=" * 60)
    print("INICIANDO PAPER TRADING ORCHESTRATOR")
    print(f"Meta: {cycles} ciclos simulados rodando em cima de DADOS REAIS.")
    print(f"Intervalo: {sleep_seconds}s entre ciclos.")
    print(f"DB path: {get_db_path()}")
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
    parser = argparse.ArgumentParser(description="Run paper trading cycles against the real local SQLite DB.")
    parser.add_argument("--cycles", type=int, default=4320, help="Number of cycles to run. Default: 4320")
    parser.add_argument("--sleep", type=int, default=60, help="Seconds between cycles. Default: 60")
    parser.add_argument("--no-backup", action="store_true", help="Skip the startup/periodic DB backup.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.no_backup:
        backup_db()
    start_paper_trading(cycles=args.cycles, sleep_seconds=args.sleep, backup=not args.no_backup)
