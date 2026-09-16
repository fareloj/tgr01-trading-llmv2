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


class _HttpError(Exception):
    """Mimics an openai APIStatusError well enough for the classifier."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class _FakeCompletions:
    def __init__(self, strict_error=None, fallback_error=None, fallback_content='{"ok":true}'):
        self.strict_error = strict_error
        self.fallback_error = fallback_error
        self.fallback_content = fallback_content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        is_fallback = kwargs.get("response_format", {}).get("type") == "json_object"
        if is_fallback:
            if self.fallback_error:
                raise self.fallback_error
            return _FakeResponse(self.fallback_content)
        if self.strict_error:
            raise self.strict_error
        return _FakeResponse(self.fallback_content)


class _FakeResponse:
    def __init__(self, content):
        message = type("Msg", (), {"content": content})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        self.choices = [choice]


class _FakeClient:
    def __init__(self, **kwargs):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(**kwargs)})()


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


class TestCredentialEndpointBinding:
    """A provider's key must never be sent to a different host."""

    def test_custom_base_url_on_the_same_host_is_allowed(self, monkeypatch):
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", "qwen3.7-plus")
        monkeypatch.setenv("NEWS_BASE_URL", "https://opencode.ai/zen/go/v1")

        assert resolve_multi_agent_model_config().news.base_url == "https://opencode.ai/zen/go/v1"

    def test_foreign_host_is_rejected(self, monkeypatch):
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", "qwen3.7-plus")
        monkeypatch.setenv("NEWS_BASE_URL", "https://evil.example.com/v1")

        with pytest.raises(ValueError, match="different endpoint"):
            resolve_multi_agent_model_config()

    def test_non_http_scheme_is_rejected(self, monkeypatch):
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", "qwen3.7-plus")
        monkeypatch.setenv("NEWS_BASE_URL", "file:///etc/passwd")

        with pytest.raises(ValueError, match="http"):
            resolve_multi_agent_model_config()

    def test_tls_downgrade_is_rejected(self, monkeypatch):
        """Hostname alone is not enough: http would expose the credential."""
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", "qwen3.7-plus")
        monkeypatch.setenv("NEWS_BASE_URL", "http://opencode.ai/v1")

        with pytest.raises(ValueError, match="origin"):
            resolve_multi_agent_model_config()

    def test_unregistered_port_is_rejected(self, monkeypatch):
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", "qwen3.7-plus")
        monkeypatch.setenv("NEWS_BASE_URL", "https://opencode.ai:444/v1")

        with pytest.raises(ValueError, match="origin"):
            resolve_multi_agent_model_config()

    def test_embedded_credentials_are_rejected(self, monkeypatch):
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", "qwen3.7-plus")
        monkeypatch.setenv("NEWS_BASE_URL", "https://user:pw@opencode.ai/v1")

        with pytest.raises(ValueError, match="credentials"):
            resolve_multi_agent_model_config()

    def test_ollama_cloud_does_not_fall_back_to_the_daemon_key(self, monkeypatch):
        """LLM_API_KEY belongs to the local daemon; it must not reach the cloud."""
        monkeypatch.setenv("LLM_API_KEY", "local-daemon-key")

        assert load_provider_api_keys("ollama-cloud") == []

    def test_ollama_cloud_accepts_its_own_key(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_CLOUD_API_KEY", "cloud-key")

        assert load_provider_api_keys("ollama-cloud") == ["cloud-key"]


class TestProviderModelAllowlist:
    def test_opencode_go_rejects_a_non_qwen_model(self, monkeypatch):
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", "kimi-k2.7-code")

        with pytest.raises(ValueError, match="not sanctioned"):
            resolve_multi_agent_model_config()

    def test_opencode_go_accepts_the_sanctioned_qwen_family(self, monkeypatch):
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", "qwen3.7-plus")

        assert resolve_multi_agent_model_config().news.model == "qwen3.7-plus"

    @pytest.mark.parametrize("lookalike", ["qwen3.7-evil", "qwen3.8anything", "qwen3.7", "qwen3.9-plus"])
    def test_opencode_go_rejects_lookalike_names(self, monkeypatch, lookalike):
        """A shared prefix must not smuggle an unsanctioned model in."""
        monkeypatch.setenv("NEWS_PROVIDER", "opencode-go")
        monkeypatch.setenv("NEWS_MODEL", lookalike)

        with pytest.raises(ValueError, match="not sanctioned"):
            resolve_multi_agent_model_config()

    def test_ollama_has_no_model_allowlist(self, monkeypatch):
        monkeypatch.setenv("NEWS_MODEL", "any-tag-at-all:cloud")

        assert resolve_multi_agent_model_config().news.model == "any-tag-at-all:cloud"


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

    def test_clients_are_cached_per_endpoint_not_just_provider(self, monkeypatch):
        """Two roles on one provider but different endpoints must not share a client."""
        monkeypatch.setenv("LLM_API_KEY", "ollama-key")
        client = StructuredAgentClient()
        first = self._role(provider="ollama", base_url="http://localhost:11434/v1", session_header=None)
        second = self._role(provider="ollama", base_url="http://127.0.0.1:11434/v1", session_header=None)

        built_first = client._client_for(first)
        built_second = client._client_for(second)

        assert built_first is not built_second
        assert len(client._clients) == 2

    def test_same_provider_and_endpoint_reuses_the_client(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "ollama-key")
        client = StructuredAgentClient()
        role = self._role(provider="ollama", base_url="http://localhost:11434/v1", session_header=None)

        assert client._client_for(role) is client._client_for(role)
        assert len(client._clients) == 1

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
        role_model = client.config.news
        client._clients[(role_model.provider, role_model.base_url.rstrip("/"))] = fake

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


class TestStrictNumericContracts:
    """A malformed model response must not be coerced into a plausible decision."""

    def test_boolean_conviction_is_rejected(self):
        from backend.agents.contracts import MultiAgentDecision

        with pytest.raises(Exception, match="boolean"):
            MultiAgentDecision.model_validate(
                {"action": "BUY", "conviction": True, "thesis": "t"}
            )

    def test_boolean_confidence_is_rejected(self):
        from backend.agents.contracts import NewsAnalysis

        with pytest.raises(Exception, match="boolean"):
            NewsAnalysis.model_validate(
                {"status": "OK", "bias": "NEUTRAL", "confidence": True, "summary": "s"}
            )

    def test_plain_integers_still_validate(self):
        from backend.agents.contracts import MultiAgentDecision

        assert MultiAgentDecision.model_validate(
            {"action": "BUY", "conviction": 70, "thesis": "t"}
        ).conviction == 70

    def test_string_conviction_is_rejected_under_strict_mode(self):
        from backend.agents.contracts import MultiAgentDecision

        with pytest.raises(Exception):
            MultiAgentDecision.model_validate(
                {"action": "BUY", "conviction": "70", "thesis": "t"}, strict=True
            )


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
        # A format mention alone is not enough; the message must say it is unsupported.
        assert not StructuredAgentClient._is_json_schema_rejection(
            FakeError("response_format field has an invalid enum value", 400)
        )
        # An unsupported claim with no format mention must not downgrade either.
        assert not StructuredAgentClient._is_json_schema_rejection(
            FakeError("this attachment type is unsupported", 400)
        )
        # A server error must not be treated as a format refusal.
        assert not StructuredAgentClient._is_json_schema_rejection(
            FakeError("response_format exploded", 500)
        )
        assert not StructuredAgentClient._is_json_schema_rejection(
            FakeError("connection reset")
        )

    def test_cause_adjacency_prevents_a_false_downgrade(self):
        """The red-team counterexample: a real cause named after the format token."""
        class FakeError(Exception):
            def __init__(self, message, status_code=None, body=None):
                super().__init__(message)
                self.status_code = status_code
                self.body = body

        assert not StructuredAgentClient._is_json_schema_rejection(
            FakeError("response_format=json_schema accepted; temperature is unsupported", 400)
        )
        # A genuine refusal is still detected.
        assert StructuredAgentClient._is_json_schema_rejection(
            FakeError("response_format: json_schema is not supported", 400)
        )
        assert StructuredAgentClient._is_json_schema_rejection(
            FakeError("unsupported response_format type json_schema", 422)
        )

    def test_structured_error_body_wins_when_it_names_the_parameter(self):
        class FakeError(Exception):
            def __init__(self, message, status_code=None, body=None):
                super().__init__(message)
                self.status_code = status_code
                self.body = body

        assert StructuredAgentClient._is_json_schema_rejection(
            FakeError(
                "provider rejected the request",
                400,
                body={"error": {"message": "bad parameter", "param": "response_format"}},
            )
        )
        # A different named parameter must not downgrade.
        assert not StructuredAgentClient._is_json_schema_rejection(
            FakeError(
                "provider rejected the request",
                400,
                body={"error": {"message": "bad parameter", "param": "temperature"}},
            )
        )

    def test_failed_fallback_does_not_poison_later_calls(self, monkeypatch):
        """The downgrade is remembered only after it produced valid output."""
        from backend.agents.contracts import NewsAnalysis

        monkeypatch.setenv("LLM_API_KEY", "ollama")
        client = StructuredAgentClient()
        role_model = client.config.news
        client._clients[(role_model.provider, role_model.base_url.rstrip("/"))] = _FakeClient(
            strict_error=_HttpError("This response_format type is unavailable now", 400),
            fallback_error=_HttpError("upstream exploded", 500),
        )

        with pytest.raises(_HttpError, match="upstream exploded"):
            client.call(
                role="news",
                role_model=role_model,
                system_prompt="prompt",
                payload={},
                schema=NewsAnalysis,
            )

        assert client._response_format_override == {}

    def test_successful_fallback_is_remembered_per_role_and_model(self, monkeypatch):
        from backend.agents.contracts import NewsAnalysis

        monkeypatch.setenv("LLM_API_KEY", "ollama")
        client = StructuredAgentClient()
        role_model = client.config.news
        client._clients[(role_model.provider, role_model.base_url.rstrip("/"))] = _FakeClient(
            strict_error=_HttpError("This response_format type is unavailable now", 400),
            fallback_content='{"status":"NO_NEWS","bias":"UNCERTAIN","confidence":0,"summary":"none"}',
        )

        output = client.call(
            role="news",
            role_model=role_model,
            system_prompt="prompt",
            payload={},
            schema=NewsAnalysis,
        )

        assert output.output.status == "NO_NEWS"
        key = (
            f"news:{role_model.model}:{role_model.provider}:"
            f"{role_model.base_url.rstrip('/')}"
        )
        assert client._response_format_override[key] == {"type": "json_object"}
        assert client.response_format_for("news") == "json_object_fallback"

    def test_downgrade_is_not_shared_across_endpoints(self, monkeypatch):
        """A different endpoint must re-probe the strict contract, not inherit it."""
        from backend.agents.contracts import NewsAnalysis

        monkeypatch.setenv("LLM_API_KEY", "ollama")
        client = StructuredAgentClient()
        first = client.config.news
        client._clients[(first.provider, first.base_url.rstrip("/"))] = _FakeClient(
            strict_error=_HttpError("This response_format type is unavailable now", 400),
            fallback_content='{"status":"NO_NEWS","bias":"UNCERTAIN","confidence":0,"summary":"none"}',
        )
        client.call(
            role="news", role_model=first, system_prompt="p", payload={}, schema=NewsAnalysis
        )

        second = RoleModel(
            role="news",
            model=first.model,
            provider=first.provider,
            base_url="http://127.0.0.1:11434/v1",
            temperature=0.0,
            max_tokens=100,
        )
        client._clients[(second.provider, second.base_url.rstrip("/"))] = _FakeClient(
            fallback_content='{"status":"NO_NEWS","bias":"UNCERTAIN","confidence":0,"summary":"none"}',
        )
        client.call(
            role="news", role_model=second, system_prompt="p", payload={}, schema=NewsAnalysis
        )

        # The second endpoint accepted json_schema, so it must not be recorded as
        # a fallback even though the first endpoint had one.
        assert len(client._response_format_override) == 1
        assert not any(second.base_url.rstrip("/") in key for key in client._response_format_override)

    def test_call_exposes_auditable_diagnostics(self, monkeypatch):
        """A fail-closed HOLD must be explainable from the recorded metadata."""
        from backend.agents.contracts import NewsAnalysis

        monkeypatch.setenv("LLM_API_KEY", "ollama")
        client = StructuredAgentClient()
        role_model = client.config.news
        client._clients[(role_model.provider, role_model.base_url.rstrip("/"))] = _FakeClient(
            fallback_content='{"status":"NO_NEWS","bias":"UNCERTAIN","confidence":0,"summary":"none"}',
        )

        call = client.call(
            role="news",
            role_model=role_model,
            system_prompt="prompt",
            payload={},
            schema=NewsAnalysis,
        )
        diagnostics = call.diagnostics()

        assert diagnostics["role"] == "news"
        assert diagnostics["provider"] == role_model.provider
        assert diagnostics["response_format"] == "json_schema"
        assert diagnostics["max_tokens"] == role_model.max_tokens
        assert diagnostics["reasoning_effort"] == role_model.reasoning_effort
        # No secret or credential may appear in the diagnostics.
        assert not any("key" in str(field).lower() for field in diagnostics.values())

    def test_json_schema_format_matches_the_contract(self):
        from backend.agents.contracts import NewsAnalysis

        response_format = StructuredAgentClient._json_schema_format("news", NewsAnalysis)

        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        assert response_format["json_schema"]["name"] == "news_output"
        assert response_format["json_schema"]["schema"] == NewsAnalysis.model_json_schema()
