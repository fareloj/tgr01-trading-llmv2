from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DB_PATH = BACKEND_DIR / "trading_v2.db"
REPORTS_DIR = BACKEND_DIR / "reports"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ops.commands import command_catalog


@dataclass(frozen=True)
class ExperimentStep:
    label: str
    args: tuple[str, ...]
    output_path: Path | None = None
    optional: bool = False


def get_next_trade_log_id(db_path: Path = DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM trade_logs").fetchone()[0])
    finally:
        conn.close()


def build_experiment_steps(*, since_id: int, cycles: int, sleep_seconds: int, include_llm_review: bool) -> list[ExperimentStep]:
    strict_preflight = command_catalog()["preflight"].args
    steps = [
        ExperimentStep("Preflight estrito", strict_preflight),
        ExperimentStep("Paper trading", ("backend/tests/run_paper_trading.py", "--cycles", str(cycles), "--sleep", str(sleep_seconds))),
        ExperimentStep(
            "Relatorio limpo de trade_logs",
            ("backend/tests/analyze_trade_logs.py", "--since-id", str(since_id), "--limit", "50"),
            REPORTS_DIR / "last_100_trade_log_report.txt",
        ),
        ExperimentStep(
            "Avaliacao deterministica por movimento futuro",
            (
                "backend/tests/evaluate_decisions.py",
                "--since-id",
                str(since_id),
                "--horizons",
                "5,15,30,60",
                "--threshold",
                "0.20",
                "--limit",
                "30",
                "--json-out",
                "backend/reports/last_100_decision_evaluation.json",
            ),
            REPORTS_DIR / "last_100_decision_evaluation.txt",
        ),
        ExperimentStep(
            "Relatorio de entradas aprovadas e bloqueadas",
            (
                "backend/tests/analyze_entry_decisions.py",
                "--since-id",
                str(since_id),
                "--horizons",
                "5,15,30,60",
                "--threshold",
                "0.20",
                "--json-out",
                "backend/reports/last_100_entry_decisions.json",
            ),
            REPORTS_DIR / "last_100_entry_decisions.txt",
        ),
    ]
    if include_llm_review:
        steps.append(
            ExperimentStep(
                "Revisao LLM auxiliar",
                (
                    "backend/tests/llm_review_decisions.py",
                    "--input",
                    "backend/reports/last_100_decision_evaluation.json",
                    "--output",
                    "backend/reports/last_100_llm_review.md",
                ),
                REPORTS_DIR / "last_100_llm_review.out.txt",
                optional=True,
            )
        )
    return steps


def run_step(step: ExperimentStep) -> int:
    print(f"\n[EXPERIMENT] {step.label}", flush=True)
    print(f"[EXPERIMENT] python {' '.join(step.args)}", flush=True)
    output_file = None
    try:
        if step.output_path:
            step.output_path.parent.mkdir(parents=True, exist_ok=True)
            output_file = step.output_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-u", *step.args],
            cwd=PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout
        for line in process.stdout:
            print(line, end="", flush=True)
            if output_file:
                output_file.write(line)
        return process.wait()
    finally:
        if output_file:
            output_file.close()


def run_experiment(*, cycles: int, sleep_seconds: int, include_llm_review: bool) -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    since_id = get_next_trade_log_id()
    (REPORTS_DIR / "last_run_since_id.txt").write_text(f"{since_id}\n", encoding="utf-8")
    print(f"[EXPERIMENT] since-id={since_id}", flush=True)
    for step in build_experiment_steps(
        since_id=since_id,
        cycles=cycles,
        sleep_seconds=sleep_seconds,
        include_llm_review=include_llm_review,
    ):
        code = run_step(step)
        if code and step.optional:
            print(f"[WARN] Etapa opcional falhou: {step.label} (codigo {code}).", flush=True)
            continue
        if code:
            print(f"[BLOCKED] Experimento interrompido em: {step.label} (codigo {code}).", flush=True)
            return code
    print("\n[OK] Experimento e relatorios concluidos.", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper trading experiment with deterministic reports.")
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--sleep", type=int, choices=(30, 60), default=60, dest="sleep_seconds")
    parser.add_argument("--skip-llm-review", action="store_false", dest="include_llm_review")
    parser.set_defaults(include_llm_review=True)
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(run_experiment(**vars(parse_args())))
