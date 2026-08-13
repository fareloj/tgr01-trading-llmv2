"""Regression coverage for provider-aware response_format selection.

LM Studio's OpenAI-compatible server only accepts response_format.type in
{"json_schema", "text"} -- the looser "json_object" mode used by hosted
Groq/OpenAI-style endpoints returns HTTP 400. DecisionAgent and
PromptProfileRunner must both pick a strict json_schema payload when talking
to a local provider (base_url pointing at localhost/127.0.0.1) and keep the
json_object shape for every hosted provider, so a historical campaign never
silently breaks depending on --news-mode/--variants routing.
"""
import pytest

from backend.agents.contracts import DecisionOutput, AnalysisPlan
from backend.agents.decision_agent import DecisionAgent
from backend.tests.compare_prompt_profiles import PromptProfileRunner


@pytest.fixture
def local_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("LLM_API_KEY", "lm-studio-local")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-20b")


@pytest.fixture
def hosted_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("LLM_API_KEY", "gsk_fake_test_key")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")


class TestDecisionAgentResponseFormat:
    def test_local_base_url_uses_json_schema(self, local_env):
        agent = DecisionAgent()
        assert agent._is_local_provider() is True

        response_format = agent._response_format(DecisionOutput, "decision_output")
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == "decision_output"
        assert response_format["json_schema"]["schema"] == DecisionOutput.model_json_schema()

    def test_local_base_url_uses_json_schema_for_planner(self, local_env):
        agent = DecisionAgent()
        response_format = agent._response_format(AnalysisPlan, "analysis_plan")
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["schema"] == AnalysisPlan.model_json_schema()

    def test_hosted_base_url_uses_json_object(self, hosted_env):
        agent = DecisionAgent()
        assert agent._is_local_provider() is False
        assert agent._response_format(DecisionOutput, "decision_output") == {"type": "json_object"}

    def test_127_0_0_1_is_treated_as_local(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
        monkeypatch.setenv("LLM_API_KEY", "lm-studio-local")
        agent = DecisionAgent()
        assert agent._is_local_provider() is True

    def test_default_base_url_is_ollama(self, monkeypatch):
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("GROQ_BASE_URL", raising=False)
        monkeypatch.setenv("LLM_API_KEY", "ollama")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        agent = DecisionAgent()
        assert agent.base_url == "http://localhost:11434/v1"
        assert agent.model == "nemotron-3-ultra:cloud"
        assert agent._is_local_provider() is True
        assert agent._response_format(DecisionOutput, "decision_output")["type"] == "json_schema"


class TestPromptProfileRunnerResponseFormat:
    def test_local_base_url_uses_json_schema(self, local_env):
        runner = PromptProfileRunner()
        assert runner._is_local_provider() is True

        response_format = runner._response_format()
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == "decision_output"
        assert response_format["json_schema"]["schema"] == DecisionOutput.model_json_schema()

    def test_hosted_base_url_uses_json_object(self, hosted_env):
        runner = PromptProfileRunner()
        assert runner._is_local_provider() is False
        assert runner._response_format() == {"type": "json_object"}
