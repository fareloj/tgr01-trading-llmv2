import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.ops import run_forward_session as forward_session
from backend.ops.run_forward_session import (
    build_collection_commands,
    build_finalization_commands,
    inspect_evaluation_report,
    new_session_id,
    parse_horizons,
    resolve_session_dir,
)


def test_forward_session_id_is_stable_utc_timestamp():
    now = datetime(2026, 8, 10, 12, 34, 56, tzinfo=timezone.utc)

    assert new_session_id(now) == "forward_20260810_123456_utc"


def test_horizons_are_positive_unique_and_sorted():
    assert parse_horizons("480,15,60,60,240") == [15, 60, 240, 480]
    with pytest.raises(ValueError):
        parse_horizons("0,60")
    with pytest.raises(ValueError):
        parse_horizons("15,not-a-number")


def test_session_id_rejects_path_traversal():
    with pytest.raises(ValueError):
        resolve_session_dir("../outside")
    with pytest.raises(ValueError):
        resolve_session_dir(Path("C:/outside/forward_20260810_123456_utc"))


def test_collection_always_starts_with_preflight_and_scopes_audit():
    commands = build_collection_commands(since_id=408, cycles=96, sleep_seconds=900)

    assert commands[0][0] == "Preflight estrito"
    assert "--require-clock-sync" in commands[0][1]
    assert commands[1][1][-4:] == ("--cycles", "96", "--sleep", "900")
    assert commands[2][1][-4:] == ("--since-id", "408", "--limit", "96")


def test_finalization_uses_session_specific_outputs(tmp_path: Path):
    commands = build_finalization_commands(
        since_id=408,
        horizons=[15, 60, 240, 480],
        session_dir=tmp_path,
    )

    assert "15,60,240,480" in commands[0][1]
    assert str(tmp_path / "decision_evaluation.json") in commands[0][1]
    assert str(tmp_path / "entry_decisions.json") in commands[1][1]
    assert commands[2][1][-2:] == ("--since-id", "408")


def test_collection_marks_session_blocked_when_preflight_fails(tmp_path: Path, monkeypatch):
    session_id = "forward_20260810_123456_utc"
    monkeypatch.setattr(forward_session, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(forward_session, "new_session_id", lambda: session_id)
    monkeypatch.setattr(forward_session.repository, "get_next_trade_log_id", lambda: 408)
    monkeypatch.setattr(
        forward_session,
        "resolve_multi_agent_model_config",
        lambda: SimpleNamespace(
            enabled=False,
            shadow_mode=True,
            news_model="news",
            technical_model="technical",
            decision_model="decision",
        ),
    )
    monkeypatch.setattr(forward_session, "run_logged", lambda *_args, **_kwargs: 7)

    assert forward_session.collect(cycles=1, sleep_seconds=900, horizons=[15]) == 7
    manifest = json.loads((tmp_path / session_id / "session.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "BLOCKED"
    assert manifest["blocked_step"] == "Preflight estrito"
    assert manifest["steps"][0]["return_code"] == 7


def test_finalization_refuses_unmatured_session(tmp_path: Path, monkeypatch):
    session_id = "forward_20260810_123456_utc"
    session_dir = tmp_path / session_id
    session_dir.mkdir()
    manifest = {
        "session_id": session_id,
        "status": "AWAITING_MATURITY",
        "finalize_after_timestamp": int(time.time()) + 600,
        "since_trade_log_id": 408,
        "horizons_minutes": [15],
        "steps": [],
    }
    (session_dir / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(forward_session, "SESSIONS_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="not mature"):
        forward_session.finalize(session_id)

    persisted = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "AWAITING_MATURITY"


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ({"logs_evaluated": 1, "summary": {"15": {"not_matured": 0, "data_gap": 0}}}, "READY"),
        ({"logs_evaluated": 1, "summary": {"15": {"not_matured": 1, "data_gap": 0}}}, "PENDING"),
        ({"logs_evaluated": 1, "summary": {"15": {"not_matured": 0, "data_gap": 1}}}, "BLOCKED"),
        ({"logs_evaluated": 0, "summary": {"15": {"not_matured": 0, "data_gap": 0}}}, "BLOCKED"),
    ],
)
def test_evaluation_report_controls_session_completion(tmp_path: Path, report: dict, expected: str):
    path = tmp_path / "evaluation.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert inspect_evaluation_report(path)[0] == expected


def test_finalize_does_not_complete_when_evaluator_reports_immature(tmp_path: Path, monkeypatch):
    session_id = "forward_20260810_123456_utc"
    session_dir = tmp_path / session_id
    session_dir.mkdir()
    manifest = {
        "session_id": session_id,
        "status": "AWAITING_MATURITY",
        "finalize_after_timestamp": int(time.time()) - 1,
        "since_trade_log_id": 408,
        "horizons_minutes": [15],
        "steps": [],
    }
    (session_dir / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(forward_session, "SESSIONS_DIR", tmp_path)
    calls = []

    def fake_run(label, _args, output_path):
        calls.append(label)
        output_path.write_text("ok", encoding="utf-8")
        (session_dir / "decision_evaluation.json").write_text(
            json.dumps(
                {
                    "logs_evaluated": 1,
                    "summary": {"15": {"not_matured": 1, "data_gap": 0}},
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(forward_session, "run_logged", fake_run)

    assert forward_session.finalize(session_id) == 3
    persisted = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "AWAITING_MATURITY"
    assert calls == ["Avaliacao futura madura"]
