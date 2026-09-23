from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping
from unicodedata import category
from urllib.parse import urlsplit


DEFAULT_API_PROVIDER = "gptsapi"
OPENAI_COMPATIBLE_API_PROVIDER = "openai_compatible"
DEEPSEEK_API_PROVIDER = "deepseek"
OPENROUTER_API_PROVIDER = "openrouter"


def _has_control_characters(value: str) -> bool:
    return any(category(character) == "Cc" for character in value)


def _normalize_base_url(value: object) -> str:
    if not isinstance(value, str) or _has_control_characters(value):
        raise ValueError("API base URL must be a string without control characters")
    normalized = value.strip().rstrip("/")
    if any(character.isspace() for character in normalized) or "\\" in normalized:
        raise ValueError("API base URL must not contain whitespace or backslashes")
    try:
        parsed = urlsplit(normalized)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and "?" not in normalized
            and "#" not in normalized
        )
        # Accessing port also validates a supplied port number.
        parsed.port
    except ValueError as exc:
        raise ValueError("API base URL is invalid") from exc
    if not valid:
        raise ValueError(
            "API base URL must be an absolute HTTP(S) URL without credentials, "
            "query, or fragment"
        )
    return normalized


@dataclass(frozen=True)
class ApiProviderConfig:
    base_url: str
    model: str
    supports_vision: bool
    disable_thinking: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _normalize_base_url(self.base_url))
        if not isinstance(self.model, str) or _has_control_characters(self.model):
            raise ValueError("API model must be a string without control characters")
        # An unconfigured generic provider has no model; saved overrides must
        # supply one, which AppSettings validates before accepting the mapping.
        object.__setattr__(self, "model", self.model.strip())
        if not isinstance(self.supports_vision, bool):
            raise ValueError("supports_vision must be a boolean")
        if not isinstance(self.disable_thinking, bool):
            raise ValueError("disable_thinking must be a boolean")


@dataclass(frozen=True)
class ApiProviderSpec:
    provider_id: str
    display_name: str
    short_name: str
    base_url: str
    model: str
    environment_variable: str
    credential_target: str
    supports_vision: bool
    disable_thinking: bool = False


_PROVIDER_SPECS = (
    ApiProviderSpec(
        provider_id=DEFAULT_API_PROVIDER,
        display_name="GPTSAPI",
        short_name="GPTSAPI",
        base_url="https://api.gptsapi.net/v1",
        model="gpt-5.4-nano",
        environment_variable="GPTSAPI_API_KEY",
        credential_target="SnipDoTranslate/GPTSAPI",
        supports_vision=True,
    ),
    ApiProviderSpec(
        provider_id=DEEPSEEK_API_PROVIDER,
        display_name="DeepSeek 官方接口",
        short_name="DeepSeek",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        environment_variable="DEEPSEEK_API_KEY",
        credential_target="SnipDoTranslate/DeepSeek",
        supports_vision=False,
        disable_thinking=True,
    ),
    ApiProviderSpec(
        provider_id=OPENROUTER_API_PROVIDER,
        display_name="OpenRouter",
        short_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-5.6-luna",
        environment_variable="OPENROUTER_API_KEY",
        credential_target="SnipDoTranslate/OpenRouter",
        supports_vision=True,
    ),
    ApiProviderSpec(
        provider_id=OPENAI_COMPATIBLE_API_PROVIDER,
        display_name="OpenAI 兼容接口（自定义）",
        short_name="OpenAI 兼容",
        base_url="https://api.openai.com/v1",
        model="",
        environment_variable="OPENAI_API_KEY",
        credential_target="SnipDoTranslate/OpenAICompatible",
        supports_vision=False,
    ),
)

API_PROVIDERS: Mapping[str, ApiProviderSpec] = MappingProxyType(
    {provider.provider_id: provider for provider in _PROVIDER_SPECS}
)
API_PROVIDER_IDS = frozenset(API_PROVIDERS)


def api_provider_options() -> tuple[ApiProviderSpec, ...]:
    return _PROVIDER_SPECS


def get_api_provider(
    provider_id: str, config: ApiProviderConfig | None = None,
) -> ApiProviderSpec:
    try:
        provider = API_PROVIDERS[provider_id]
    except (KeyError, TypeError) as exc:
        raise ValueError("unsupported API provider") from exc
    if config is None:
        return provider
    if not isinstance(config, ApiProviderConfig):
        raise ValueError("API configuration must be an ApiProviderConfig")
    return replace(
        provider,
        base_url=config.base_url,
        model=config.model,
        supports_vision=config.supports_vision,
        disable_thinking=config.disable_thinking,
    )


def provider_default_config(provider_id: str) -> ApiProviderConfig:
    provider = get_api_provider(provider_id)
    return ApiProviderConfig(
        base_url=provider.base_url,
        model=provider.model,
        supports_vision=provider.supports_vision,
        disable_thinking=provider.disable_thinking,
    )


def chat_completion_options(
    provider: ApiProviderSpec,
    *,
    streaming: bool = False,
) -> dict[str, object]:
    options: dict[str, object] = {"model": provider.model}
    if streaming:
        options["extra_headers"] = {"Accept": "text/event-stream"}
    if provider.disable_thinking:
        options["extra_body"] = {"thinking": {"type": "disabled"}}
    return options


__all__ = [
    "API_PROVIDERS",
    "API_PROVIDER_IDS",
    "DEFAULT_API_PROVIDER",
    "DEEPSEEK_API_PROVIDER",
    "OPENAI_COMPATIBLE_API_PROVIDER",
    "OPENROUTER_API_PROVIDER",
    "ApiProviderConfig",
    "ApiProviderSpec",
    "api_provider_options",
    "chat_completion_options",
    "get_api_provider",
    "provider_default_config",
]
