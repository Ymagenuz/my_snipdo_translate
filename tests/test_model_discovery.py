from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, OpenAI
from openai._base_client import PageInfo
from openai.pagination import SyncPage

from model_discovery import fetch_available_models


@contextmanager
def sdk_client(handler):
    """Exercise the SDK's actual request and response handling without a network."""
    with OpenAI(
        api_key="test-only-key",
        base_url="https://models.example.test/custom/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        yield client


def model_list(*model_ids):
    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "created": 0, "owned_by": "provider"}
            for model_id in model_ids
        ],
    }


def test_lists_models_from_configured_api_and_preserves_distinct_ids():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=model_list(
                "provider/model:free", " custom-model ", "Model-A", "model-a", "custom-model"
            ),
        )

    with sdk_client(handle) as client:
        assert fetch_available_models(client) == (
            "Model-A", "custom-model", "model-a", "provider/model:free"
        )

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == "https://models.example.test/custom/v1/models"
    assert requests[0].headers["authorization"] == "Bearer test-only-key"


def test_uses_sdk_iterator_to_include_subsequent_pages(monkeypatch):
    # models.list currently uses an unpaginated SyncPage. Give that real SDK
    # page pagination metadata to verify discovery consumes its full iterator.
    def next_page_info(page):
        cursor = getattr(page, "next_cursor", None)
        return PageInfo(params={"after": cursor}) if cursor else None

    monkeypatch.setattr(SyncPage, "next_page_info", next_page_info)
    cursors = []

    def handle(request):
        cursor = request.url.params.get("after")
        cursors.append(cursor)
        if cursor is None:
            return httpx.Response(
                200, json={**model_list("z-model", "shared"), "next_cursor": "page-2"}
            )
        assert cursor == "page-2"
        return httpx.Response(200, json=model_list("shared", "a-model"))

    with sdk_client(handle) as client:
        assert fetch_available_models(client) == ("a-model", "shared", "z-model")
    assert cursors == [None, "page-2"]


def test_ignores_malformed_and_control_character_model_ids():
    payload = model_list(
        " valid/model ", "", "   ", None, 42, [], {}, "bad\nmodel",
        "bad\x00model", "bad\x7fmodel", "bad\x85model", "bad\u200bmodel", "\tbad-model",
    )
    payload["data"].extend([{}, None, "not-a-model"])

    with sdk_client(lambda _request: httpx.Response(200, json=payload)) as client:
        assert fetch_available_models(client) == ("valid/model",)


@pytest.mark.parametrize(
    "payload", [model_list(), model_list("", None, "bad\nmodel"), {"data": None}, {}]
)
def test_empty_or_unusable_model_list_has_actionable_error(payload):
    with sdk_client(lambda _request: httpx.Response(200, json=payload)) as client:
        with pytest.raises(ValueError, match="no available models.*Enter a model ID manually"):
            fetch_available_models(client)


@pytest.mark.parametrize("payload", [{"data": 123}])
def test_invalid_model_list_has_actionable_error(payload):
    with sdk_client(lambda _request: httpx.Response(200, json=payload)) as client:
        with pytest.raises(ValueError, match="invalid model list.*Enter a model ID manually"):
            fetch_available_models(client)


@pytest.mark.parametrize("status_code", [401, 403, 404, 429, 500])
def test_preserves_api_errors_for_the_callers_error_display(status_code):
    def handle(_request):
        return httpx.Response(status_code, json={"error": {"message": "Provider rejected request"}})

    with sdk_client(handle) as client:
        with pytest.raises(APIStatusError) as caught:
            fetch_available_models(client)
    assert caught.value.status_code == status_code


def test_preserves_connection_errors_for_the_callers_error_display():
    def handle(request):
        raise httpx.ConnectError("Connection failed", request=request)

    with sdk_client(handle) as client:
        with pytest.raises(APIConnectionError):
            fetch_available_models(client)
