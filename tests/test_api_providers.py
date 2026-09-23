from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from api_providers import (
    API_PROVIDER_IDS,
    DEFAULT_API_PROVIDER,
    DEEPSEEK_API_PROVIDER,
    OPENAI_COMPATIBLE_API_PROVIDER,
    ApiProviderConfig,
    api_provider_options,
    chat_completion_options,
    get_api_provider,
    provider_default_config,
)


def test_generic_provider_has_independent_identity_and_credentials():
    generic = get_api_provider(OPENAI_COMPATIBLE_API_PROVIDER)
    gptsapi = get_api_provider(DEFAULT_API_PROVIDER)

    assert DEFAULT_API_PROVIDER == "gptsapi"
    assert gptsapi.display_name == "GPTSAPI"
    assert gptsapi.base_url == "https://api.gptsapi.net/v1"
    assert gptsapi.model == "gpt-5.4-nano"
    assert gptsapi.environment_variable == "GPTSAPI_API_KEY"
    assert gptsapi.credential_target == "SnipDoTranslate/GPTSAPI"
    assert generic.base_url == "https://api.openai.com/v1"
    assert generic.model == ""
    assert generic.environment_variable == "OPENAI_API_KEY"
    assert generic.credential_target == "SnipDoTranslate/OpenAICompatible"
    assert generic.supports_vision is False
    assert generic in api_provider_options()


@pytest.mark.parametrize("provider_id", sorted(API_PROVIDER_IDS))
def test_default_configuration_reconstructs_provider(provider_id: str):
    default = provider_default_config(provider_id)

    assert get_api_provider(provider_id, default) == get_api_provider(provider_id)


def test_resolving_configuration_preserves_provider_credential_identity():
    original = get_api_provider(DEEPSEEK_API_PROVIDER)
    custom = ApiProviderConfig(
        " https://example.org/custom/v1/// ",
        " chosen-model ",
        True,
        False,
    )

    resolved = get_api_provider(DEEPSEEK_API_PROVIDER, custom)

    assert resolved.provider_id == original.provider_id
    assert resolved.environment_variable == original.environment_variable
    assert resolved.credential_target == original.credential_target
    assert resolved.base_url == "https://example.org/custom/v1"
    assert resolved.model == "chosen-model"
    assert resolved.supports_vision is True
    assert resolved.disable_thinking is False
    assert chat_completion_options(resolved) == {"model": "chosen-model"}
    assert get_api_provider(DEEPSEEK_API_PROVIDER) == original


def test_configured_thinking_option_is_used_in_request_options():
    provider = get_api_provider(
        OPENAI_COMPATIBLE_API_PROVIDER,
        ApiProviderConfig("http://localhost:8080/v1", "local-model", False, True),
    )

    assert chat_completion_options(provider, streaming=True) == {
        "model": "local-model",
        "extra_headers": {"Accept": "text/event-stream"},
        "extra_body": {"thinking": {"type": "disabled"}},
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:1234/v1/",
        "https://example.org/v1",
        "http://127.0.0.1:8080",
        "http://[::1]:8080/v1",
    ],
)
def test_configuration_accepts_remote_and_local_http_endpoints(url: str):
    assert ApiProviderConfig(url, "model", False).base_url == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        None,
        123,
        "",
        "/v1",
        "example.org/v1",
        "ftp://example.org/v1",
        "https:///v1",
        "https://",
        "https://user:secret@example.org/v1",
        "https://user@example.org/v1",
        "https://@example.org/v1",
        "https://example.org/v1?api_key=secret",
        "https://example.org/v1?",
        "https://example.org/v1#secret",
        "https://example.org/v1#",
        "https://example.org:wrong/v1",
        "https://example.org:70000/v1",
        "https://[::1/v1",
        "https://example.org/my path",
        "https://example.org\\v1",
        "https://example.org/\n",
        "\thttps://example.org/v1",
        "https://example.org/\x00",
        "https://example.org/\x7f",
        "https://example.org/\x85",
    ],
)
def test_configuration_rejects_invalid_or_secret_bearing_urls(url: object):
    with pytest.raises(ValueError):
        ApiProviderConfig(url, "model", False)  # type: ignore[arg-type]


@pytest.mark.parametrize("model", [None, 1, "model\n", "model\x7f", "model\x85"])
def test_configuration_rejects_invalid_models(model: object):
    with pytest.raises(ValueError):
        ApiProviderConfig("https://example.org/v1", model, False)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, 1, None, "false", [], {}])
@pytest.mark.parametrize("field", ["supports_vision", "disable_thinking"])
def test_configuration_requires_boolean_options(field: str, value: object):
    fields = {
        "base_url": "https://example.org/v1",
        "model": "model",
        "supports_vision": False,
        "disable_thinking": False,
        field: value,
    }

    with pytest.raises(ValueError):
        ApiProviderConfig(**fields)  # type: ignore[arg-type]


def test_configuration_is_immutable():
    config = provider_default_config(DEFAULT_API_PROVIDER)

    with pytest.raises(FrozenInstanceError):
        config.model = "replacement"  # type: ignore[misc]


@pytest.mark.parametrize("provider_id", [None, [], "missing"])
def test_provider_lookup_rejects_unknown_provider(provider_id: object):
    with pytest.raises(ValueError, match="unsupported API provider"):
        get_api_provider(provider_id)  # type: ignore[arg-type]


def test_provider_lookup_rejects_non_config_override():
    with pytest.raises(ValueError, match="ApiProviderConfig"):
        get_api_provider(DEFAULT_API_PROVIDER, {})  # type: ignore[arg-type]
