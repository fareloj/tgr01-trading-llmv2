"""Regression coverage for gpt-oss family detection across naming conventions.

gpt-oss is a heavy chain-of-thought reasoner: without reasoning_effort=low and a larger
completion budget it reliably spends its whole token budget on internal reasoning and
returns empty/truncated content (observed directly against both LM Studio's
"openai/gpt-oss-20b" tag and Ollama Cloud's "gpt-oss:120b-cloud" tag). DecisionAgent and
PromptProfileRunner must recognize the gpt-oss family under either naming convention and
grant it the larger budget -- silently falling back to the tiny default budget produces a
~60% empty/truncated-JSON failure rate even at temperature=0.
"""
import pytest

from backend.agents.decision_agent import DecisionAgent
from backend.tests.compare_prompt_profiles import PromptProfileRunner


GPT_OSS_MODEL_TAGS = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "gpt-oss:20b",
    "gpt-oss:120b-cloud",
]

NON_GPT_OSS_MODEL_TAGS = [
    "llama-3.3-70b-versatile",
    "qwen/qwen3.5-9b",
    "prism-ml/bonsai-27b",
]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.delenv("GROQ_BASE_URL", raising=False)


class TestDecisionAgentGptOssDetection:
    @pytest.mark.parametrize("model", GPT_OSS_MODEL_TAGS)
    def test_gpt_oss_family_gets_the_larger_reasoning_budget(self, env, monkeypatch, model):
        monkeypatch.setenv("LLM_MODEL", model)
        agent = DecisionAgent()
        limits = agent._request_limits("decision")
        assert limits["reasoning_effort"] == "low"
        assert limits["max_completion_tokens"] >= 3000

    @pytest.mark.parametrize("model", NON_GPT_OSS_MODEL_TAGS)
    def test_non_gpt_oss_models_keep_the_default_budget(self, env, monkeypatch, model):
        monkeypatch.setenv("LLM_MODEL", model)
        agent = DecisionAgent()
        limits = agent._request_limits("decision")
        assert limits == {"max_tokens": 450}


class TestPromptProfileRunnerGptOssDetection:
    @pytest.mark.parametrize("model", GPT_OSS_MODEL_TAGS)
    def test_gpt_oss_family_gets_the_larger_reasoning_budget(self, env, monkeypatch, model):
        monkeypatch.setenv("LLM_MODEL", model)
        runner = PromptProfileRunner()
        limits = runner._request_limits()
        assert limits == {"max_completion_tokens": 600, "reasoning_effort": "low"}

    @pytest.mark.parametrize("model", NON_GPT_OSS_MODEL_TAGS)
    def test_non_gpt_oss_models_keep_the_default_budget(self, env, monkeypatch, model):
        monkeypatch.setenv("LLM_MODEL", model)
        runner = PromptProfileRunner()
        assert runner._request_limits() == {"max_tokens": 450}
