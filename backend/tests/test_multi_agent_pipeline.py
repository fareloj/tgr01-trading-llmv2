import pytest

from backend.agents.contracts import MultiAgentDecision, NewsAnalysis, TechnicalAnalysis
from backend.agents.multi_agent_pipeline import MultiAgentAnalysisPipeline
from backend.tests.run_multi_agent_historical_campaign import sample_completed


def test_news_evidence_must_reference_input_ids():
    report = NewsAnalysis(
        status="OK",
        bias="NEUTRAL",
        confidence=40,
        summary="No directional consensus.",
        evidence_news_ids=["missing"],
    )

    with pytest.raises(ValueError, match="absent"):
        MultiAgentAnalysisPipeline._validate_news_evidence(report, [{"id": "known"}])


def test_empty_news_requires_no_news_report():
    report = NewsAnalysis(
        status="OK",
        bias="NEUTRAL",
        confidence=20,
        summary="No records.",
    )

    with pytest.raises(ValueError, match="NO_NEWS"):
        MultiAgentAnalysisPipeline._validate_news_evidence(report, [])


def test_technical_evidence_rejects_unknown_field_roots():
    report = TechnicalAnalysis(
        status="OK",
        regime="MIXED",
        direction="NEUTRAL",
        confidence=40,
        summary="Mixed evidence.",
        evidence_fields=["secret_future_price"],
        news_alignment="UNRELATED",
    )

    with pytest.raises(ValueError, match="allowlist"):
        MultiAgentAnalysisPipeline._validate_technical_evidence(report, {"status": "OK"})


@pytest.mark.parametrize("stale_field", ["is_market_data_stale", "has_untrusted_instruction"])
def test_decision_fails_closed_for_hard_safety_conditions(stale_field):
    snapshot = {
        "data_health": {"is_market_data_stale": stale_field == "is_market_data_stale"},
        "news_risk": {"has_untrusted_instruction": stale_field == "has_untrusted_instruction"},
    }
    report = MultiAgentDecision(action="BUY", conviction=60, thesis="Candidate setup.")

    with pytest.raises(ValueError):
        MultiAgentAnalysisPipeline._validate_decision(report, snapshot)


def test_stale_news_caps_directional_conviction():
    snapshot = {
        "data_health": {"is_market_data_stale": False, "is_news_stale": True},
        "news_risk": {},
    }
    report = MultiAgentDecision(action="SELL", conviction=70, thesis="Candidate setup.")

    with pytest.raises(ValueError, match="stale-news cap"):
        MultiAgentAnalysisPipeline._validate_decision(report, snapshot)


def test_decision_evidence_must_exist_in_accepted_inputs():
    snapshot = {
        "technical_context": {"rsi": {"value": 45.0}},
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {},
    }
    report = MultiAgentDecision(
        action="HOLD",
        conviction=40,
        thesis="No directional edge.",
        evidence_fields=["future.unknown_price"],
    )

    with pytest.raises(ValueError, match="absent"):
        MultiAgentAnalysisPipeline._validate_decision(report, snapshot)


def test_resume_only_accepts_complete_role_chain():
    valid = {role: {"output": {}} for role in ("news", "technical", "decision")}
    invalid = {**valid, "decision": {"error": "provider timeout"}}

    assert sample_completed(valid) is True
    assert sample_completed(invalid) is False
