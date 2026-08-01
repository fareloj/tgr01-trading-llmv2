import os
import subprocess
import sys
import time
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_DIR / "backend" / "logs"


def _worker_is_running(script_name: str) -> bool:
    escaped = script_name.replace("'", "''")
    command = (
        "@(Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.Name -match 'python' -and $_.CommandLine -match '{escaped}' }}).Count"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() not in {"", "0"}


def _start_worker(script: Path, args: list[str], log_stem: str) -> bool:
    stdout_path = LOG_DIR / f"{log_stem}.out.log"
    stderr_path = LOG_DIR / f"{log_stem}.err.log"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open("a", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-u", str(script), *args],
            cwd=PROJECT_DIR,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
    time.sleep(2)
    if process.poll() is not None:
        print(f"[FAIL] {log_stem} encerrou durante a inicializacao. Consulte {stderr_path}.")
        return False
    print(f"[OK] {log_stem} iniciado com {sys.executable} (PID {process.pid}).")
    return True


def start_workers() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    healthy = True
    workers = [
        ("price_worker.py", PROJECT_DIR / "backend" / "data" / "price_worker.py", [], "price_worker"),
        (
            "news_worker.py",
            PROJECT_DIR / "backend" / "data" / "news_worker.py",
            ["--mode", "real", "--interval", "900"],
            "news_worker",
        ),
    ]
    for script_name, script_path, args, log_stem in workers:
        if _worker_is_running(script_name):
            print(f"[OK] {log_stem} ja esta rodando.")
            continue
        healthy = _start_worker(script_path, args, log_stem) and healthy

    print("Aguarde 30-60s e rode o preflight estrito.")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(start_workers())
