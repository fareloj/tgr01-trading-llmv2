from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ops.commands import CommandSpec, command_catalog


RunCommand = Callable[..., subprocess.CompletedProcess]


def build_execution_plan(action: str, since_id: str) -> list[CommandSpec]:
    catalog = command_catalog(since_id)
    if action not in catalog:
        raise ValueError(f"Acao operacional nao permitida: {action}")
    requested = catalog[action]
    if requested.requires_preflight:
        return [catalog["preflight"], requested]
    return [requested]


def execute_action(action: str, since_id: str, *, run_command: RunCommand = subprocess.run) -> int:
    plan = build_execution_plan(action, since_id)
    for index, spec in enumerate(plan, start=1):
        print(f"\n[OPS {index}/{len(plan)}] {spec.label}", flush=True)
        print(f"[OPS] {spec.description}", flush=True)
        print(f"[OPS] python {' '.join(spec.args)}", flush=True)
        result = run_command(
            [sys.executable, "-u", *spec.args],
            cwd=PROJECT_DIR,
            check=False,
        )
        if result.returncode:
            print(f"[OPS] BLOQUEADO: {spec.label} retornou codigo {result.returncode}.", flush=True)
            return result.returncode
    print(f"\n[OPS] Fluxo concluido: {action}", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one allowlisted TGR-01 operational action.")
    parser.add_argument("action", choices=sorted(command_catalog()))
    parser.add_argument("--since-id", default="1")
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(execute_action(**vars(parse_args())))
