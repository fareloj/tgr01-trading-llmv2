"""Model-role configuration for the experimental multi-agent pipeline.

Design notes
------------
Every role (news, technical, decision) resolves to a `RoleModel`: a model id plus
a provider. The provider decides the base URL, which credential environment
variables to read, and whether the endpoint needs the `x-opencode-session`
header. A role can therefore mix providers in one pipeline.

Model ids are provider specific: the Ollama daemon (and its cloud proxy) uses the
`:cloud` suffix, while the direct Ollama Cloud API and OpenCode Go use the bare
name. Keep the id exactly as the chosen provider expects it.

Quota policy
------------
OpenCode Go is a paid subscription with per-model monthly caps. In this project
only the Qwen 3.7/3.8 family is sanctioned on that provider; the default roles
deliberately stay on Ollama so routine paper cycles never consume Go quota.

Validated on 2026-09-16 against the real Pydantic contracts (see
`backend/tests/test_model_providers.py`):

  provider       model                temp  json_schema  notes
  ollama         glm-5.3:cloud        0.0   yes          news + technical
  ollama         glm-5.3-flash:cloud  0.0   yes          faster, same contracts
  ollama         kimi-k2.7-code:cloud 0.0   yes          decision
  opencode-go    qwen3.7-plus         0.0   yes          needs session header

Not every hosted endpoint accepts the same request shape. `deepseek-v4.1-flash`
on OpenCode Go rejects `json_schema` outright, and `glm-5.3` there returns an
empty body under a small token budget. The client therefore negotiates the
response format per model and keeps a generous completion budget.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_LLM_BASE_URL = "http://localhost:11434/v1"
OLLAMA_CLOUD_BASE_URL = "https://ollama.com/v1"
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_SESSION_HEADER = "x-opencode-session"

# The local Ollama daemon proxies Ollama Cloud models and is the default because
# it needs no extra credential beyond the existing LLM_API_KEY.
DEFAULT_PROVIDER = "ollama"

DEFAULT_NEWS_MODEL = "glm-5.3:cloud"
DEFAULT_TECHNICAL_MODEL = "glm-5.3:cloud"
DEFAULT_DECISION_MODEL = "kimi-k2.7-code:cloud"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 5000

# Measured on 2026-09-16 against the real role contracts (12 samples per role).
# `reasoning_effort=low` cut glm-5.3 wall time roughly 7x (28s vs 195s) and raised
# valid contract output from 11/12 to 12/12, because the model otherwise spent a
# full 5000-token budget on hidden reasoning and returned an empty body.
# Kimi K2.7 is different: it wants a larger budget (12/12 at 8000 vs 10/12 at
# 5000) and scored better WITHOUT the effort override (12/12 vs 11/12), so the
# decision role leaves it unset. Do not apply an effort override blindly to a
# new model: measure it first, as an unsupported value can produce empty output.
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_DECISION_MAX_TOKENS = 8000

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

# Providers sanctioned for this repository. Adding a provider here is the only
# way a role can be pointed at a new endpoint.
_KNOWN_PROVIDERS = ("ollama", "ollama-cloud", "opencode-go")


@dataclass(frozen=True)
class ProviderSpec:
    """Endpoint facts for one provider. Contains no credentials."""

    name: str
    base_url: str
    api_key_envs: tuple[str, ...]
    session_header: str | None = None
    local: bool = False


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "ollama": ProviderSpec(
        name="ollama",
        base_url=DEFAULT_LLM_BASE_URL,
        api_key_envs=("LLM_API_KEY", "GROQ_API_KEY"),
        local=True,
    ),
    "ollama-cloud": ProviderSpec(
        name="ollama-cloud",
        base_url=OLLAMA_CLOUD_BASE_URL,
        # Deliberately no LLM_API_KEY fallback: that variable holds the local
        # daemon credential, and reusing it here would send one provider's
        # secret to a different host.
        api_key_envs=("OLLAMA_CLOUD_API_KEY", "OLLAMA_API_KEY"),
    ),
    "opencode-go": ProviderSpec(
        name="opencode-go",
        base_url=OPENCODE_GO_BASE_URL,
        api_key_envs=("OPENCODE_GO_API_KEY",),
        session_header=OPENCODE_GO_SESSION_HEADER,
    ),
}

# Models sanctioned per provider, as anchored regular expressions. A role may not
# resolve a model outside this list, so a typo cannot spend quota elsewhere.
PROVIDER_MODEL_ALLOWLIST: dict[str, tuple[str, ...]] = {
    # The local daemon proxies any Ollama Cloud tag, so no restriction here.
    "ollama": (),
    "ollama-cloud": (),
    # OpenCode Go is a paid subscription with per-model monthly caps. Only the
    # Qwen 3.7/3.8 family is sanctioned for this project. The pattern enumerates
    # the sanctioned variants and is matched with fullmatch, so a lookalike such
    # as "qwen3.7-evil" or "qwen3.8anything" cannot pass by sharing a prefix.
    # Add a variant here explicitly when one is approved.
    "opencode-go": (
        r"qwen3\.7-(?:max|plus|flash)",
        r"qwen3\.8-(?:max|plus|flash)",
    ),
}


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


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


def resolve_provider(name: str) -> ProviderSpec:
    """Return the provider spec, failing closed on an unknown provider name."""
    normalized = (name or "").strip().lower()
    if normalized not in PROVIDER_REGISTRY:
        raise ValueError(
            f"unknown model provider {name!r}; expected one of: "
            f"{', '.join(_KNOWN_PROVIDERS)}"
        )
    return PROVIDER_REGISTRY[normalized]


def _endpoint_origin(url: str) -> tuple[str, str, int | None]:
    """Return (scheme, hostname, port) with the scheme's default port filled in."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, host, port


def resolve_allowed_base_url(provider: str, override: str) -> str:
    """Bind a base URL override to its provider's registered origin.

    A role supplies `<ROLE>_PROVIDER` for the credential and `<ROLE>_BASE_URL` for
    the endpoint. Without this check a typo in the URL would send one provider's
    API key, and its session header, to an arbitrary host.

    The whole origin must match: scheme, hostname and effective port. Checking only
    the hostname would still allow a TLS downgrade (`http://`) or an unregistered
    port, both of which expose the credential. Embedded userinfo is rejected too.
    """
    spec = resolve_provider(provider)
    candidate = (override or "").strip()
    if not candidate:
        return spec.base_url

    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"base URL for provider {provider!r} must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"base URL for provider {provider!r} must not embed credentials")

    if _endpoint_origin(candidate) != _endpoint_origin(spec.base_url):
        raise ValueError(
            f"base URL origin {_endpoint_origin(candidate)!r} does not match provider "
            f"{provider!r} origin {_endpoint_origin(spec.base_url)!r}; refusing to send "
            f"this provider's credential to a different endpoint"
        )
    return candidate


def _check_model_sanctioned(role: str, provider: str, model: str) -> None:
    """Refuse a model outside the provider's sanctioned list.

    The check uses anchored patterns so a lookalike name such as
    `qwen3.7-evil` cannot pass by sharing a prefix with a sanctioned model.
    """
    allowed = PROVIDER_MODEL_ALLOWLIST.get(provider, ())
    if not allowed:
        return
    normalized = (model or "").strip().lower()
    if any(re.fullmatch(pattern, normalized) for pattern in allowed):
        return
    raise ValueError(
        f"{role.upper()}_MODEL {model!r} is not sanctioned for provider "
        f"{provider!r}; expected an exact sanctioned id matching one of: "
        f"{', '.join(allowed)}"
    )


def assert_role_request_allowed(
    *,
    role: str,
    provider: str,
    model: str,
    base_url: str,
) -> None:
    """Validate the three values that decide where a credential is sent.

    `_resolve_role` calls this at startup, and `StructuredAgentClient` calls it
    again at the point of use. The second check matters because `RoleModel` is a
    plain dataclass and `call()` accepts a `model` override: without revalidating
    at the sink, a constructed or overridden value could bypass the startup
    checks and send a provider key to an unregistered endpoint.

    An empty `base_url` is treated as "use the registered default".
    """
    spec = resolve_provider(provider)
    resolve_allowed_base_url(spec.name, base_url or spec.base_url)
    _check_model_sanctioned(role, spec.name, model)


def load_provider_api_keys(provider: str) -> list[str]:
    """Read credentials for one provider using the same conventions as the agent.

    Supports `<PREFIX>S` (comma/semicolon/newline separated), `<PREFIX>`, and
    `<PREFIX>_1..10`. Returns an empty list when nothing is configured; the
    caller decides whether a missing key is fatal.
    """
    spec = resolve_provider(provider)
    for env_prefix in spec.api_key_envs:
        keys: list[str] = []
        raw_many = os.getenv(f"{env_prefix}S", "")
        if raw_many:
            keys.extend(part.strip() for part in re.split(r"[,;\n]+", raw_many) if part.strip())
        single = os.getenv(env_prefix, "").strip()
        if single:
            keys.append(single)
        for index in range(1, 11):
            candidate = os.getenv(f"{env_prefix}_{index}", "").strip()
            if candidate:
                keys.append(candidate)

        unique: list[str] = []
        seen = set()
        for key in keys:
            if key not in seen:
                unique.append(key)
                seen.add(key)
        if unique:
            return unique
    return []


@dataclass(frozen=True)
class RoleModel:
    """A resolved model assignment for one pipeline role."""

    role: str
    model: str
    provider: str
    base_url: str
    temperature: float
    max_tokens: int
    reasoning_effort: str | None = None
    session_header: str | None = None

    @property
    def is_local(self) -> bool:
        return resolve_provider(self.provider).local


def _resolve_role(
    role: str,
    default_model: str,
    default_max_tokens: int,
    default_reasoning_effort: str | None = None,
) -> RoleModel:
    """Resolve one role from the environment.

    Per-role variables (uppercase role name):
      <ROLE>_MODEL, <ROLE>_PROVIDER, <ROLE>_BASE_URL, <ROLE>_TEMPERATURE,
      <ROLE>_MAX_TOKENS, <ROLE>_REASONING_EFFORT

    `decision` additionally honours the canonical `LLM_MODEL` because the
    single-agent runtime uses it, and `news`/`technical` keep their historical
    `NEWS_AGENT_MODEL`/`TECHNICAL_AGENT_MODEL` names.
    """
    upper = role.upper()
    legacy_model_env = {
        "decision": "LLM_MODEL",
        "news": "NEWS_AGENT_MODEL",
        "technical": "TECHNICAL_AGENT_MODEL",
    }.get(role)

    model = _env_text(f"{upper}_MODEL", "")
    if not model and legacy_model_env:
        model = _env_text(legacy_model_env, "")
    if not model:
        model = default_model

    provider = _env_text(f"{upper}_PROVIDER", "").lower()
    if not provider:
        # Historical global default; `LLM_BASE_URL` alone is not enough to name a
        # provider, so only fall back to it when it matches a known registry entry.
        base = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
        provider = {
            OLLAMA_CLOUD_BASE_URL: "ollama-cloud",
            OPENCODE_GO_BASE_URL: "opencode-go",
            DEFAULT_LLM_BASE_URL: "ollama",
        }.get(base, DEFAULT_PROVIDER)

    spec = resolve_provider(provider)
    base_url = resolve_allowed_base_url(provider, _env_text(f"{upper}_BASE_URL", ""))
    _check_model_sanctioned(role, spec.name, model)
    temperature = _env_float(f"{upper}_TEMPERATURE", DEFAULT_TEMPERATURE)
    max_tokens = _env_int(f"{upper}_MAX_TOKENS", default_max_tokens)
    reasoning_effort = _env_text(f"{upper}_REASONING_EFFORT", default_reasoning_effort or "").lower() or None

    if temperature < 0:
        raise ValueError(f"{upper}_TEMPERATURE must be non-negative")
    if max_tokens <= 0:
        raise ValueError(f"{upper}_MAX_TOKENS must be positive")
    if reasoning_effort not in (None, "none", "low", "medium", "high"):
        raise ValueError(f"{upper}_REASONING_EFFORT must be one of: none, low, medium, high")

    return RoleModel(
        role=role,
        model=model,
        provider=spec.name,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        session_header=spec.session_header,
    )


@dataclass(frozen=True)
class MultiAgentModelConfig:
    """Resolved model assignments without credentials or execution authority."""

    enabled: bool
    shadow_mode: bool
    base_url: str
    news_model: str
    technical_model: str
    decision_model: str
    news: RoleModel | None = None
    technical: RoleModel | None = None
    decision: RoleModel | None = None

    @property
    def may_influence_paper_decisions(self) -> bool:
        return self.enabled and not self.shadow_mode

    def role(self, name: str) -> RoleModel:
        """Return the resolved RoleModel for `news`, `technical` or `decision`."""
        resolved = {"news": self.news, "technical": self.technical, "decision": self.decision}.get(name)
        if resolved is None:
            raise ValueError(f"unknown role {name!r}")
        return resolved

    def roles(self) -> dict[str, RoleModel]:
        return {
            "news": self.role("news"),
            "technical": self.role("technical"),
            "decision": self.role("decision"),
        }


def resolve_multi_agent_model_config() -> MultiAgentModelConfig:
    """Resolve role assignments from environment variables.

    The feature is disabled and shadow-only by default. Merely configuring the
    model names never grants execution authority.
    """

    news = _resolve_role("news", DEFAULT_NEWS_MODEL, DEFAULT_MAX_TOKENS, DEFAULT_REASONING_EFFORT)
    technical = _resolve_role("technical", DEFAULT_TECHNICAL_MODEL, DEFAULT_MAX_TOKENS, DEFAULT_REASONING_EFFORT)
    decision = _resolve_role("decision", DEFAULT_DECISION_MODEL, DEFAULT_DECISION_MAX_TOKENS, None)

    return MultiAgentModelConfig(
        enabled=_env_bool("MULTI_AGENT_ENABLED", False),
        shadow_mode=_env_bool("MULTI_AGENT_SHADOW_MODE", True),
        base_url=_env_text("LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        news_model=news.model,
        technical_model=technical.model,
        decision_model=decision.model,
        news=news,
        technical=technical,
        decision=decision,
    )
