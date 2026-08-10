import pytest

from backend.agents.model_config import (
    DEFAULT_DECISION_MODEL,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_NEWS_MODEL,
    DEFAULT_TECHNICAL_MODEL,
    resolve_multi_agent_model_config,
)


_CONFIG_ENV_VARS = (
    "LLM_BASE_URL",
    "LLM_MODEL",
    "MULTI_AGENT_ENABLED",
    "MULTI_AGENT_SHADOW_MODE",
    "NEWS_AGENT_MODEL",
    "TECHNICAL_AGENT_MODEL",
)


@pytest.fixture(autouse=True)
def clean_model_config_env(monkeypatch):
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_disabled_and_shadow_only():
    config = resolve_multi_agent_model_config()

    assert config.enabled is False
    assert config.shadow_mode is True
    assert config.may_influence_paper_decisions is False
    assert config.base_url == DEFAULT_LLM_BASE_URL
    assert config.news_model == DEFAULT_NEWS_MODEL
    assert config.technical_model == DEFAULT_TECHNICAL_MODEL
    assert config.decision_model == DEFAULT_DECISION_MODEL


def test_default_role_assignments_match_experimental_cloud_models():
    config = resolve_multi_agent_model_config()

    assert config.news_model == "gpt-oss:20b-cloud"
    assert config.technical_model == "qwen3.5:122b-cloud"
    assert config.decision_model == "gpt-oss:120b-cloud"


def test_role_models_and_endpoint_can_be_overridden(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("NEWS_AGENT_MODEL", "news-test")
    monkeypatch.setenv("TECHNICAL_AGENT_MODEL", "technical-test")
    monkeypatch.setenv("LLM_MODEL", "decision-test")

    config = resolve_multi_agent_model_config()

    assert config.base_url == "http://127.0.0.1:11434/v1"
    assert config.news_model == "news-test"
    assert config.technical_model == "technical-test"
    assert config.decision_model == "decision-test"


def test_active_non_shadow_configuration_is_explicit(monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "true")
    monkeypatch.setenv("MULTI_AGENT_SHADOW_MODE", "false")

    config = resolve_multi_agent_model_config()

    assert config.enabled is True
    assert config.shadow_mode is False
    assert config.may_influence_paper_decisions is True


def test_invalid_boolean_fails_closed(monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "maybe")

    with pytest.raises(ValueError, match="MULTI_AGENT_ENABLED"):
        resolve_multi_agent_model_config()
