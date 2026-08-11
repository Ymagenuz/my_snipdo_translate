from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


DEFAULT_API_PROVIDER = "gptsapi"
DEEPSEEK_API_PROVIDER = "deepseek"


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
        display_name="OpenAI 兼容（GPTSAPI）",
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
)

API_PROVIDERS: Mapping[str, ApiProviderSpec] = MappingProxyType(
    {provider.provider_id: provider for provider in _PROVIDER_SPECS}
)
API_PROVIDER_IDS = frozenset(API_PROVIDERS)


def api_provider_options() -> tuple[ApiProviderSpec, ...]:
    return _PROVIDER_SPECS


def get_api_provider(provider_id: str) -> ApiProviderSpec:
    try:
        return API_PROVIDERS[provider_id]
    except (KeyError, TypeError) as exc:
        raise ValueError("unsupported API provider") from exc


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
    "ApiProviderSpec",
    "api_provider_options",
    "chat_completion_options",
    "get_api_provider",
]
