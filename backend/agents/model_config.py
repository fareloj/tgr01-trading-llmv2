"""Model-role configuration for the experimental multi-agent pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_LLM_BASE_URL = "http://localhost:11434/v1"
DEFAULT_DECISION_MODEL = "gpt-oss:120b-cloud"
DEFAULT_NEWS_MODEL = "gemma4:31b-cloud"
DEFAULT_TECHNICAL_MODEL = "qwen3.5:122b-cloud"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of: 1/0, true/false, yes/no, on/off")


@dataclass(frozen=True)
class MultiAgentModelConfig:
    """Resolved model assignments without credentials or execution authority."""

    enabled: bool
    shadow_mode: bool
    base_url: str
    news_model: str
    technical_model: str
    decision_model: str

    @property
    def may_influence_paper_decisions(self) -> bool:
        return self.enabled and not self.shadow_mode


def resolve_multi_agent_model_config() -> MultiAgentModelConfig:
    """Resolve role assignments from environment variables.

    The feature is disabled and shadow-only by default. Merely configuring the
    model names never grants execution authority.
    """

    return MultiAgentModelConfig(
        enabled=_env_bool("MULTI_AGENT_ENABLED", False),
        shadow_mode=_env_bool("MULTI_AGENT_SHADOW_MODE", True),
        base_url=_env_text("LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        news_model=_env_text("NEWS_AGENT_MODEL", DEFAULT_NEWS_MODEL),
        technical_model=_env_text("TECHNICAL_AGENT_MODEL", DEFAULT_TECHNICAL_MODEL),
        decision_model=_env_text("LLM_MODEL", DEFAULT_DECISION_MODEL),
    )
