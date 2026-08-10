"""Run auditable forward paper sessions in collection and maturity phases."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_DIR / "backend"
SESSIONS_DIR = BACKEND_DIR / "reports" / "forward_sessions"
sys.path.insert(0, str(PROJECT_DIR))

from backend.agents.model_config import resolve_multi_agent_model_config
from backend.core import repository
from backend.core.market_policy import DECISION_INTERVAL_SECONDS, MARKET_DATA_MAX_AGE_SECONDS
from backend.ops.commands import command_catalog


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def new_session_id(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return current.strftime("forward_%Y%m%d_%H%M%S_utc")


def resolve_session_dir(value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if not resolved.is_relative_to(SESSIONS_DIR.resolve()):
            raise ValueError("absolute session path must be inside the forward sessions directory")
        return resolved
    if candidate.parent != Path("."):
        raise ValueError("relative session paths are not allowed")
    if not re.fullmatch(r"forward_[0-9]{8}_[0-9]{6}_utc", str(value)):
        raise ValueError("invalid forward session id")
    return (SESSIONS_DIR / candidate).resolve()


def parse_horizons(value: str) -> list[int]:
    try:
        horizons = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    except ValueError as error:
        raise ValueError("horizons must be comma-separated integers") from error
    if not horizons or horizons[0] <= 0 or horizons[-1] > 1440:
        raise ValueError("horizons must be between 1 and 1440 minutes")
    return horizons


def inspect_evaluation_report(path: Path) -> tuple[str, str | None]:
    """Require every requested observation to be mature before completion."""
    report = json.loads(path.read_text(encoding="utf-8"))
    if int(report.get("logs_evaluated", 0)) <= 0:
        return "BLOCKED", "future evaluation contains no trade logs"
    summary = report.get("summary")
    if not isinstance(summary, dict) or not summary:
        return "BLOCKED", "future evaluation summary is missing"
    not_matured = sum(int(item.get("not_matured", 0)) for item in summary.values())
    data_gaps = sum(int(item.get("data_gap", 0)) for item in summary.values())
    if data_gaps:
        return "BLOCKED", f"future evaluation contains {data_gaps} candle data gaps"
    if not_matured:
        return "PENDING", f"future evaluation contains {not_matured} immature observations"
    return "READY", None


def run_logged(label: str, args: tuple[str, ...], output_path: Path) -> int:
    print(f"\n[FORWARD] {label}", flush=True)
    print(f"[FORWARD] python {' '.join(args)}", flush=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [sys.executable, "-u", *args],
            cwd=PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            output.write(line)
        return process.wait()


def build_collection_commands(*, since_id: int, cycles: int, sleep_seconds: int) -> list[tuple[str, tuple[str, ...], str]]:
    return [
        ("Preflight estrito", command_catalog()["preflight"].args, "01_preflight.txt"),
        (
            "Coleta paper",
            (
                "backend/tests/run_paper_trading.py",
                "--cycles",
                str(cycles),
                "--sleep",
                str(sleep_seconds),
            ),
            "02_paper_collection.txt",
        ),
        (
            "Auditoria imediata",
            (
                "backend/tests/analyze_trade_logs.py",
                "--since-id",
                str(since_id),
                "--limit",
                str(max(30, cycles)),
            ),
            "03_immediate_audit.txt",
        ),
    ]


def build_finalization_commands(*, since_id: int, horizons: list[int], session_dir: Path) -> list[tuple[str, tuple[str, ...], str]]:
    horizon_text = ",".join(str(item) for item in horizons)
    return [
        (
            "Avaliacao futura madura",
            (
                "backend/tests/evaluate_decisions.py",
                "--since-id",
                str(since_id),
                "--horizons",
                horizon_text,
                "--threshold",
                "0.20",
                "--limit",
                "100",
                "--json-out",
                str(session_dir / "decision_evaluation.json"),
            ),
            "04_matured_evaluation.txt",
        ),
        (
            "Entradas aprovadas e bloqueadas",
            (
                "backend/tests/analyze_entry_decisions.py",
                "--since-id",
                str(since_id),
                "--horizons",
                horizon_text,
                "--threshold",
                "0.20",
                "--json-out",
                str(session_dir / "entry_decisions.json"),
            ),
            "05_entry_analysis.txt",
        ),
        (
            "Readiness da sessao",
            ("backend/tests/trading_readiness_report.py", "--since-id", str(since_id)),
            "06_readiness.txt",
        ),
    ]


def collect(*, cycles: int, sleep_seconds: int, horizons: list[int]) -> int:
    if cycles <= 0 or cycles > 500:
        raise ValueError("cycles must be between 1 and 500")
    if sleep_seconds < DECISION_INTERVAL_SECONDS:
        raise ValueError(
            "forward collection interval must be at least "
            f"{DECISION_INTERVAL_SECONDS} seconds"
        )

    session_id = new_session_id()
    session_dir = resolve_session_dir(session_id)
    if session_dir.exists():
        raise RuntimeError(f"session already exists: {session_dir}")
    session_dir.mkdir(parents=True)
    model_config = resolve_multi_agent_model_config()
    started_at = int(time.time())
    manifest = {
        "schema_version": 1,
        "session_id": session_id,
        "status": "COLLECTING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_timestamp": started_at,
        "since_trade_log_id": repository.get_next_trade_log_id(),
        "cycles": cycles,
        "sleep_seconds": sleep_seconds,
        "horizons_minutes": horizons,
        "execution_mode": "PAPER",
        "real_trading_enabled": False,
        "active_runtime": "single_agent_paper_pipeline",
        "multi_agent_shadow_config": {
            "enabled": model_config.enabled,
            "shadow_mode": model_config.shadow_mode,
            "news_model": model_config.news_model,
            "technical_model": model_config.technical_model,
            "decision_model": model_config.decision_model,
        },
        "steps": [],
    }
    manifest_path = session_dir / "session.json"
    atomic_write_json(manifest_path, manifest)

    for label, args, filename in build_collection_commands(
        since_id=manifest["since_trade_log_id"],
        cycles=cycles,
        sleep_seconds=sleep_seconds,
    ):
        code = run_logged(label, args, session_dir / filename)
        manifest["steps"].append({"label": label, "return_code": code, "output": filename})
        atomic_write_json(manifest_path, manifest)
        if code:
            manifest["status"] = "BLOCKED"
            manifest["blocked_step"] = label
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            atomic_write_json(manifest_path, manifest)
            return code

    completed_timestamp = int(time.time())
    manifest["status"] = "AWAITING_MATURITY"
    manifest["collection_completed_timestamp"] = completed_timestamp
    manifest["finalize_after_timestamp"] = (
        completed_timestamp
        + max(horizons) * 60
        + MARKET_DATA_MAX_AGE_SECONDS
        + 90
    )
    atomic_write_json(manifest_path, manifest)
    print(f"\n[OK] Coleta concluida: {session_id}")
    print(f"[WAIT] Finalize depois de {datetime.fromtimestamp(manifest['finalize_after_timestamp']).isoformat()}.")
    return 0


def finalize(session: str | Path, *, allow_immature: bool = False) -> int:
    session_dir = resolve_session_dir(session)
    manifest_path = session_dir / "session.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["status"] == "COMPLETED":
        print(f"[OK] Sessao ja finalizada: {manifest['session_id']}")
        return 0
    if manifest["status"] != "AWAITING_MATURITY":
        raise RuntimeError(f"session cannot be finalized from status {manifest['status']}")
    now = int(time.time())
    if now < int(manifest["finalize_after_timestamp"]) and not allow_immature:
        remaining = int(manifest["finalize_after_timestamp"]) - now
        raise RuntimeError(f"future horizons are not mature; wait {remaining} seconds")

    manifest["status"] = "FINALIZING"
    atomic_write_json(manifest_path, manifest)
    for label, args, filename in build_finalization_commands(
        since_id=int(manifest["since_trade_log_id"]),
        horizons=[int(item) for item in manifest["horizons_minutes"]],
        session_dir=session_dir,
    ):
        code = run_logged(label, args, session_dir / filename)
        manifest["steps"].append({"label": label, "return_code": code, "output": filename})
        atomic_write_json(manifest_path, manifest)
        if code:
            manifest["status"] = "BLOCKED"
            manifest["blocked_step"] = label
            atomic_write_json(manifest_path, manifest)
            return code
        if filename == "04_matured_evaluation.txt":
            evaluation_status, detail = inspect_evaluation_report(
                session_dir / "decision_evaluation.json"
            )
            if evaluation_status == "PENDING":
                manifest["status"] = "AWAITING_MATURITY"
                manifest["last_finalize_attempt"] = datetime.now(timezone.utc).isoformat()
                manifest["finalize_after_timestamp"] = int(time.time()) + 60
                manifest["pending_reason"] = detail
                atomic_write_json(manifest_path, manifest)
                print(f"[WAIT] {detail}. Tente novamente em pelo menos 60 segundos.")
                return 3
            if evaluation_status == "BLOCKED":
                manifest["status"] = "BLOCKED"
                manifest["blocked_step"] = label
                manifest["blocked_reason"] = detail
                atomic_write_json(manifest_path, manifest)
                print(f"[BLOQUEADO] {detail}.")
                return 4
    manifest["status"] = "COMPLETED"
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(manifest_path, manifest)
    print(f"[OK] Sessao forward finalizada: {manifest['session_id']}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect and finalize auditable forward paper sessions.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--cycles", type=int, default=96)
    collect_parser.add_argument(
        "--sleep",
        type=int,
        default=DECISION_INTERVAL_SECONDS,
        dest="sleep_seconds",
    )
    collect_parser.add_argument("--horizons", default="15,60,240,480")
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--session", required=True)
    finalize_parser.add_argument("--allow-immature", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "collect":
        return collect(
            cycles=args.cycles,
            sleep_seconds=args.sleep_seconds,
            horizons=parse_horizons(args.horizons),
        )
    return finalize(args.session, allow_immature=args.allow_immature)


if __name__ == "__main__":
    raise SystemExit(main())
