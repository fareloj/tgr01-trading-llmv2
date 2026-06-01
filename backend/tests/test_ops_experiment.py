from backend.ops.run_experiment import build_experiment_steps


def test_experiment_starts_with_strict_preflight_and_writes_reports():
    steps = build_experiment_steps(since_id=400, cycles=100, sleep_seconds=30, include_llm_review=True)

    assert steps[0].label == "Preflight estrito"
    assert "--require-clock-sync" in steps[0].args
    assert steps[1].args[-4:] == ("--cycles", "100", "--sleep", "30")
    assert any(step.output_path and step.output_path.name == "last_100_entry_decisions.txt" for step in steps)
    assert steps[-1].optional is True


def test_experiment_can_skip_optional_llm_review():
    steps = build_experiment_steps(since_id=400, cycles=10, sleep_seconds=60, include_llm_review=False)

    assert all(step.label != "Revisao LLM auxiliar" for step in steps)
