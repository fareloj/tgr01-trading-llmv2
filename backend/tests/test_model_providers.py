"""Contract tests for the multi-provider role configuration.

These cover the wiring only: provider resolution, credential lookup, session
header injection and the response_format fallback. They never call a network
provider, so they run in CI without quota or credentials.

The live compatibility matrix is documented in `backend/agents/model_config.py`
and was probed on 2026-09-16 against the real Pydantic contracts:

  provider       model                temp  json_schema
  ollama         glm-5.3:cloud        0.0   accepted
  ollama         glm-5.3-flash:cloud  0.0   accepted
  ollama         kimi-k2.7-code:cloud 0.0   accepted
  opencode-go    qwen3.7-plus         0.0   accepted (needs session header)
"""

import pytest

from backend.agents.model_config import (
    DEFAULT_DECISION_MAX_TOKENS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NEWS_MODEL,
    DEFAULT_REASONING_EFFORT,
    OPENCODE_GO_BASE_URL,
    OPENCODE_GO_SESSION_HEADER,
    OLLAMA_CLOUD_BASE_URL,
    RoleModel,
    load_provider_api_keys,
    resolve_multi_agent_model_config,
    resolve_provider,
)
from backend.agents.multi_agent_pipeline import StructuredAgentClient


ROLE_ENV_VARS = (
    "NEWS_MODEL", "NEWS_AGENT_MODEL", "NEWS_PROVIDER", "NEWS_BASE_URL",
    "NEWS_TEMPERATURE", "NEWS_MAX_TOKENS", "NEWS_REASONING_EFFORT",
    "TECHNICAL_MODEL", "TECHNICAL_AGENT_MODEL", "TECHNICAL_PROVIDER",
    "TECHNICAL_BASE_URL", "TECHNICAL_TEMPERATURE", "TECHNICAL_MAX_TOKENS",
    "TECHNICAL_REASONING_EFFORT",
    "DECISION_MODEL", "DECISION_PROVIDER", "DECISION_BASE_URL",
    "DECISION_TEMPERATURE", "DECISION_MAX_TOKENS", "DECISION_REASONING_EFFORT",
    "LLM_MODEL", "LLM_BASE_URL",
    "OLLAMA_CLOUD_API_KEY", "OLLAMA_API_KEY", "OPENCODE_GO_API_KEY",
    "LLM_API_KEY", "LLM_API_KEYS", "GROQ_API_KEY",
    "OPENCODE_GO_SESSION_ID",
)


@pytest.fixture(autouse=True)
def clean_role_env(monkeypatch):
    for name in ROLE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class TestProviderRegistry:
    def test_unknown_provider_fails_closed(self):
        with pytest.raises(ValueError, match="unknown model provider"):
            resolve_provider("definitely-not-a-provider")

    def test_only_sanctioned_providers_are_registered(self):
        for name in ("ollama", "ollama-cloud", "opencode-go"):
            assert resolve_provider(name).name == name

    def test_opencode_go_declares_its_session_header(self):
        spec = resolve_provider("opencode-go")

        assert spec.session_header == OPENCODE_GO_SESSION_HEADER
        assert spec.base_url == OPENCODE_GO_BASE_URL
        assert spec.api_key_envs == ("OPENCODE_GO_API_KEY",)

    def test_ollama_cloud_points_at_the_direct_api(self):
        spec = resolve_provider("ollama-cloud")

        assert spec.base_url == OLLAMA_CLOUD_BASE_URL
        assert spec.local is False


class TestCredentialLookup:
    def test_single_key_is_found(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-key")

        assert load_provider_api_keys("opencode-go") == ["go-key"]

    def test_multiple_keys_are_split_and_deduplicated(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_GO_API_KEYS", "a,b;c\na")

        assert load_provider_api_keys("opencode-go") == ["a", "b", "c"]

    def test_missing_credentials_return_empty_list(self):
        assert load_provider_api_keys("opencode-go") == []

    def test_ollama_falls_back_to_the_legacy_groq_key(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "legacy")

        assert load_provider_api_keys("ollama") == ["legacy"]


class TestRoleResolution:
    def test_defaults_are_ollama_and_do_not_spend_go_quota(self):
        config = resolve_multi_agent_model_config()

        assert config.news.model == DEFAULT_NEWS_MODEL
        for role_model in config.roles().values():
            assert role_model.provider == "ollama"
            assert role_model.temperature == 0.0

    def test_default_budget_and_effort_are_measured_per_role(self):
        """Budgets come from live measurements, not a single global constant."""
        config = resolve_multi_agent_model_config()

        assert config.news.max_tokens == DEFAULT_MAX_TOKENS
        assert config.technical.max_tokens == DEFAULT_MAX_TOKENS
        # Kimi K2.7 scored 12/12 at 8000 vs 10/12 at 5000.
        assert config.decision.max_tokens == DEFAULT_DECISION_MAX_TOKENS

        # glm roles benefit from a low reasoning effort; kimi scored better without it.
        assert config.news.reasoning_effort == DEFAULT_REASONING_EFFORT
        assert config.technical.reasoning_effort == DEFAULT_REASONING_EFFORT
        assert config.decision.reasoning_effort is None

    def test_roles_can_mix_providers_independently(self, monkeypatch):
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", "qwen3.7-plus")
        monkeypatch.setenv("TECHNICAL_PROVIDER", "ollama-cloud")
        monkeypatch.setenv("TECHNICAL_MODEL", "glm-5.3")
        monkeypatch.setenv("DECISION_PROVIDER", "ollama")

        config = resolve_multi_agent_model_config()

        assert config.news.provider == "opencode-go"
        assert config.news.model == "qwen3.7-plus"
        assert config.news.base_url == OPENCODE_GO_BASE_URL
        assert config.news.session_header == OPENCODE_GO_SESSION_HEADER
        assert config.technical.provider == "ollama-cloud"
        assert config.technical.base_url == OLLAMA_CLOUD_BASE_URL
        assert config.technical.session_header is None
        assert config.decision.provider == "ollama"

    def test_temperature_and_budget_are_per_role(self, monkeypatch):
        monkeypatch.setenv("DECISION_TEMPERATURE", "1.0")
        monkeypatch.setenv("DECISION_MAX_TOKENS", "9000")

        decision = resolve_multi_agent_model_config().decision

        assert decision.temperature == 1.0
        assert decision.max_tokens == 9000

    def test_negative_temperature_is_rejected(self, monkeypatch):
        monkeypatch.setenv("NEWS_TEMPERATURE", "-0.5")

        with pytest.raises(ValueError, match="NEWS_TEMPERATURE"):
            resolve_multi_agent_model_config()

    def test_non_positive_budget_is_rejected(self, monkeypatch):
        monkeypatch.setenv("NEWS_MAX_TOKENS", "0")

        with pytest.raises(ValueError, match="NEWS_MAX_TOKENS"):
            resolve_multi_agent_model_config()

    def test_reasoning_effort_can_be_overridden_per_role(self, monkeypatch):
        monkeypatch.setenv("DECISION_REASONING_EFFORT", "high")

        assert resolve_multi_agent_model_config().decision.reasoning_effort == "high"

    def test_invalid_reasoning_effort_fails_closed(self, monkeypatch):
        monkeypatch.setenv("NEWS_REASONING_EFFORT", "turbo")

        with pytest.raises(ValueError, match="NEWS_REASONING_EFFORT"):
            resolve_multi_agent_model_config()

    def test_legacy_llm_base_url_still_selects_the_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", OLLAMA_CLOUD_BASE_URL)

        config = resolve_multi_agent_model_config()

        assert config.decision.provider == "ollama-cloud"

    def test_unknown_role_provider_fails_closed(self, monkeypatch):
        monkeypatch.setenv("NEWS_PROVIDER", "some-other-vendor")

        with pytest.raises(ValueError, match="unknown model provider"):
            resolve_multi_agent_model_config()


class TestClientWiring:
    def _role(self, **overrides) -> RoleModel:
        base = dict(
            role="news",
            model="qwen3.7-plus",
            provider="opencode-go",
            base_url=OPENCODE_GO_BASE_URL,
            temperature=0.0,
            max_tokens=4000,
            session_header=OPENCODE_GO_SESSION_HEADER,
        )
        base.update(overrides)
        return RoleModel(**base)

    def test_missing_credential_fails_closed_with_a_clear_message(self):
        client = StructuredAgentClient()

        with pytest.raises(RuntimeError, match="no credential configured"):
            client._client_for(self._role())

    def test_session_header_is_sent_to_opencode_go(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-key")
        monkeypatch.setenv("OPENCODE_GO_SESSION_ID", "my-session")
        client = StructuredAgentClient()

        built = client._client_for(self._role())

        assert built.default_headers[OPENCODE_GO_SESSION_HEADER] == "my-session"

    def test_ollama_role_needs_no_session_header(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "ollama")
        client = StructuredAgentClient()

        built = client._client_for(
            self._role(provider="ollama", base_url="http://localhost:11434/v1", session_header=None)
        )

        # The SDK always injects its own default headers; assert only that no
        # provider session header was added on top of them.
        assert OPENCODE_GO_SESSION_HEADER not in built.default_headers

    def test_gpt_oss_keeps_the_reasoning_budget_shape(self):
        limits = StructuredAgentClient._request_limits(
            self._role(model="gpt-oss:120b-cloud", max_tokens=3000)
        )

        assert limits == {"max_completion_tokens": 3000, "reasoning_effort": "low"}

    def test_non_gpt_oss_uses_plain_max_tokens(self):
        limits = StructuredAgentClient._request_limits(self._role(model="glm-5.3:cloud", max_tokens=5000))

        assert limits == {"max_tokens": 5000}

    def test_legacy_model_string_call_still_resolves_the_role(self, monkeypatch):
        """The campaign runner passes `model=<str>`; that path must keep working."""
        monkeypatch.setenv("LLM_API_KEY", "ollama")
        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                raise RuntimeError("stop before any network use")

        client = StructuredAgentClient()
        fake = type("FakeClient", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()})()
        client._clients[client.config.news.provider] = fake

        from backend.agents.contracts import NewsAnalysis

        with pytest.raises(RuntimeError, match="stop before any network use"):
            client.call(
                role="news",
                model=client.config.news.model,
                system_prompt="prompt",
                payload={},
                schema=NewsAnalysis,
            )

        # The role's own provider settings (temperature/budget), not the caller's,
        # must drive the request.
        assert captured["temperature"] == client.config.news.temperature
        assert captured["max_tokens"] == client.config.news.max_tokens


class TestResponseFormatFallback:
    def test_rejection_detection_matches_only_format_errors(self):
        class FakeError(Exception):
            def __init__(self, message, status_code=None):
                super().__init__(message)
                self.status_code = status_code

        assert StructuredAgentClient._is_json_schema_rejection(
            FakeError("This response_format type is unavailable now", 400)
        )
        assert StructuredAgentClient._is_json_schema_rejection(
            FakeError("json_schema is not supported by this model", 422)
        )
        # An unrelated 400 must not trigger a silent downgrade.
        assert not StructuredAgentClient._is_json_schema_rejection(
            FakeError("invalid temperature: only 1 is allowed", 400)
        )
        # A server error must not be treated as a format refusal.
        assert not StructuredAgentClient._is_json_schema_rejection(
            FakeError("response_format exploded", 500)
        )
        assert not StructuredAgentClient._is_json_schema_rejection(
            FakeError("connection reset")
        )

    def test_json_schema_format_matches_the_contract(self):
        from backend.agents.contracts import NewsAnalysis

        response_format = StructuredAgentClient._json_schema_format("news", NewsAnalysis)

        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        assert response_format["json_schema"]["name"] == "news_output"
        assert response_format["json_schema"]["schema"] == NewsAnalysis.model_json_schema()
