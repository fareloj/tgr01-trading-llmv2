from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
ACTIVE_RUNTIME_FILES = (
    "backend/main.py",
    "backend/agents/decision_agent.py",
    "backend/features/payload_builder.py",
    "backend/risk/risk_manager.py",
    "backend/execution/paper_simulator.py",
    "backend/ops/commands.py",
    "backend/tui.py",
    "desktop/src/main.jsx",
    "desktop/electron/operations.cjs",
)


def test_archived_tcn_is_absent_from_active_runtime():
    for relative_path in ACTIVE_RUNTIME_FILES:
        source = (PROJECT_DIR / relative_path).read_text(encoding="utf-8")
        assert "backend.ml" not in source, relative_path
        assert "TCNAdvisor" not in source, relative_path
