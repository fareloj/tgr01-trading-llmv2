import pytest

from backend.agents.contracts import MultiAgentDecision, NewsAnalysis, TechnicalAnalysis
from backend.agents.multi_agent_pipeline import MultiAgentAnalysisPipeline, StructuredAgentClient
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


def test_news_without_source_ids_gets_deterministic_evidence_ids():
    source = [
        {"headline": "First", "source": "wire"},
        {"id": "publisher-2", "headline": "Second", "source": "wire"},
        {"id": "publisher-2", "headline": "Duplicate", "source": "wire"},
    ]

    prepared = MultiAgentAnalysisPipeline._prepare_news_context(source)

    assert [row["id"] for row in prepared] == [
        "snapshot-news-1",
        "publisher-2",
        "snapshot-news-3",
    ]
    assert "id" not in source[0]


def test_new_cloud_role_models_get_contract_safe_budgets(monkeypatch):
    monkeypatch.delenv("MULTI_AGENT_DEEPSEEK_MAX_TOKENS", raising=False)
    monkeypatch.delenv("MULTI_AGENT_GLM_MAX_TOKENS", raising=False)

    assert StructuredAgentClient._request_limits("deepseek-v4-flash:cloud") == {
        "max_tokens": 3000
    }
    assert StructuredAgentClient._request_limits("glm-5.2:cloud") == {
        "max_tokens": 5000
    }


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
