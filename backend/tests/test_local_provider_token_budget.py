"""Regression coverage for the generic local-provider token budget widening.

Every locally/self-hosted reasoning model exercised against this harness so far --
prism-ml/bonsai-27b (Q1_0), qwen/qwen3.5-9b (Q8_0), gpt-oss under the Ollama "gpt-oss:*"
tag, and nvidia's nemotron-3-ultra:cloud -- reliably spent the entire 450-token hosted-Groq
default completion budget on hidden chain-of-thought reasoning and returned empty or
truncated JSON (finish_reason=length, content=""). This is not specific to any one model
family: DecisionAgent and PromptProfileRunner must grant ANY local-provider model (base_url
pointing at localhost/127.0.0.1) a materially larger budget than the hosted-Groq default,
independent of model name. gpt-oss keeps its own even larger, purpose-tuned budget with
reasoning_effort=low; this generic path is the fallback for every other local model.
"""
import pytest

from backend.agents.decision_agent import DecisionAgent
from backend.tests.compare_prompt_profiles import PromptProfileRunner


LOCAL_NON_GPT_OSS_MODEL_TAGS = [
    "nemotron-3-super:cloud",
    "prism-ml/bonsai-27b",
    "qwen/qwen3.5-9b",
]


@pytest.fixture
def local_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "ollama-local")


@pytest.fixture
def hosted_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("LLM_API_KEY", "gsk_fake_test_key")


class TestDecisionAgentLocalBudget:
    def test_nemotron_ultra_gets_contract_safe_budget(self, local_env, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "nemotron-3-ultra:cloud")
        agent = DecisionAgent()
        assert agent._request_limits("decision") == {"max_tokens": 5000}
        assert agent._request_limits("planner") == {"max_tokens": 3500}

    def test_nemotron_ultra_budget_can_be_overridden(self, local_env, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "nemotron-3-ultra:cloud")
        monkeypatch.setenv("NEMOTRON_MAX_COMPLETION_TOKENS", "6000")
        monkeypatch.setenv("NEMOTRON_PLANNER_MAX_COMPLETION_TOKENS", "4000")
        agent = DecisionAgent()
        assert agent._request_limits("decision") == {"max_tokens": 6000}
        assert agent._request_limits("planner") == {"max_tokens": 4000}

    @pytest.mark.parametrize("model", LOCAL_NON_GPT_OSS_MODEL_TAGS)
    def test_local_non_gpt_oss_models_get_the_widened_budget(self, local_env, monkeypatch, model):
        monkeypatch.setenv("LLM_MODEL", model)
        agent = DecisionAgent()
        assert agent._request_limits("decision") == {"max_tokens": 1500}
        assert agent._request_limits("planner") == {"max_tokens": 900}

    @pytest.mark.parametrize("model", LOCAL_NON_GPT_OSS_MODEL_TAGS)
    def test_hosted_non_gpt_oss_models_keep_the_small_default(self, hosted_env, monkeypatch, model):
        monkeypatch.setenv("LLM_MODEL", model)
        agent = DecisionAgent()
        assert agent._request_limits("decision") == {"max_tokens": 450}
        assert agent._request_limits("planner") == {"max_tokens": 300}

    def test_local_gpt_oss_keeps_its_own_larger_dedicated_budget(self, local_env, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "gpt-oss:120b-cloud")
        agent = DecisionAgent()
        limits = agent._request_limits("decision")
        assert limits["reasoning_effort"] == "low"
        assert limits["max_completion_tokens"] >= 3000


class TestPromptProfileRunnerLocalBudget:
    def test_nemotron_ultra_gets_contract_safe_budget(self, local_env, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "nemotron-3-ultra:cloud")
        runner = PromptProfileRunner()
        assert runner._request_limits() == {"max_tokens": 5000}

    def test_nemotron_ultra_campaign_budget_can_be_overridden(self, local_env, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "nemotron-3-ultra:cloud")
        monkeypatch.setenv("NEMOTRON_MAX_COMPLETION_TOKENS", "6000")
        runner = PromptProfileRunner()
        assert runner._request_limits() == {"max_tokens": 6000}

    @pytest.mark.parametrize("model", LOCAL_NON_GPT_OSS_MODEL_TAGS)
    def test_local_non_gpt_oss_models_get_the_widened_budget(self, local_env, monkeypatch, model):
        monkeypatch.setenv("LLM_MODEL", model)
        runner = PromptProfileRunner()
        assert runner._request_limits() == {"max_tokens": 1500}

    @pytest.mark.parametrize("model", LOCAL_NON_GPT_OSS_MODEL_TAGS)
    def test_hosted_non_gpt_oss_models_keep_the_small_default(self, hosted_env, monkeypatch, model):
        monkeypatch.setenv("LLM_MODEL", model)
        runner = PromptProfileRunner()
        assert runner._request_limits() == {"max_tokens": 450}

    def test_local_gpt_oss_keeps_its_own_larger_dedicated_budget(self, local_env, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "gpt-oss:120b-cloud")
        runner = PromptProfileRunner()
        assert runner._request_limits() == {"max_completion_tokens": 600, "reasoning_effort": "low"}
