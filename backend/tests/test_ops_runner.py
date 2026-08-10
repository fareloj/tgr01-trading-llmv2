from pathlib import Path
import subprocess
import sys

import pytest

from backend.ops.commands import command_catalog, safe_since_id
from backend.ops.run_action import build_execution_plan, execute_action
from backend.ops.process_control import terminate_process_tree
from backend.tui import TUI_ACTION_ROWS


class FakeResult:
    def __init__(self, returncode: int):
        self.returncode = returncode


def test_safe_since_id_rejects_invalid_values():
    assert safe_since_id("303") == "303"
    assert safe_since_id("0") == "1"
    assert safe_since_id("-1") == "1"
    assert safe_since_id("abc") == "1"


def test_paper_action_is_always_preceded_by_strict_preflight():
    plan = build_execution_plan("paper30", "303")
    assert [step.label for step in plan] == ["Preflight estrito", "Paper 30 ciclos"]
    assert "--require-clock-sync" in plan[0].args
    assert "--require-workers" in plan[0].args


def test_paper_30_60_preserves_selected_interval():
    plan = build_execution_plan("paper30_60", "303")

    assert plan[1].args[-4:] == ("--cycles", "30", "--sleep", "60")


def test_report_action_does_not_add_preflight():
    plan = build_execution_plan("entries", "303")
    assert len(plan) == 1
    assert plan[0].args[0] == "backend/tests/analyze_entry_decisions.py"
    assert "303" in plan[0].args


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError):
        build_execution_plan("arbitrary-shell-command", "1")


def test_failed_preflight_stops_paper_execution():
    calls: list[tuple[list[str], Path]] = []

    def fake_run(args, *, cwd, check):
        calls.append((args, cwd))
        return FakeResult(7)

    assert execute_action("paper10", "1", run_command=fake_run) == 7
    assert len(calls) == 1
    assert "--max-kline-age-seconds" not in calls[0][0]


def test_successful_preflight_allows_paper_execution():
    calls = []

    def fake_run(args, *, cwd, check):
        calls.append(args)
        return FakeResult(0)

    assert execute_action("paper10", "1", run_command=fake_run) == 0
    assert len(calls) == 2
    assert calls[1][-4:] == ["--cycles", "10", "--sleep", "30"]


def test_catalog_only_contains_python_script_paths():
    for spec in command_catalog("303").values():
        assert spec.args
        assert spec.args[0].endswith(".py")


def test_tui_exposes_the_complete_operational_catalog():
    tui_actions = {action for row in TUI_ACTION_ROWS for _label, action in row}
    assert tui_actions == set(command_catalog("303"))


def test_windows_process_stop_targets_the_complete_tree():
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return FakeResult(0)

    terminate_process_tree(4321, platform="win32", run_command=fake_run)

    assert calls[0][0] == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert calls[0][1]["check"] is False


def test_database_diagnostics_entrypoint_runs_from_project_root():
    result = subprocess.run(
        [sys.executable, "backend/core/database.py"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PostgreSQL" in result.stdout
