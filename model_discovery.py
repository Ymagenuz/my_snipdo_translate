from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI


def fetch_available_models(api_client: OpenAI) -> tuple[str, ...]:
    """Return usable model IDs advertised by an OpenAI-compatible API.

    Iterating the SDK response also follows pagination when the SDK endpoint
    supports it. API and transport errors propagate to the caller so the UI can
    present its usual connection or authentication error.
    """
    response = api_client.models.list()
    model_ids: set[str] = set()
    try:
        for model in response:
            model_id = getattr(model, "id", None)
            if not isinstance(model_id, str):
                continue
            if any(
                unicodedata.category(character) in {"Cc", "Cf"}
                for character in model_id
            ):
                continue
            model_id = model_id.strip()
            if model_id:
                model_ids.add(model_id)
    except (AttributeError, TypeError) as exc:
        raise ValueError(
            "The API returned an invalid model list. Enter a model ID manually "
            "or check the API base URL."
        ) from exc

    if not model_ids:
        raise ValueError(
            "The API returned no available models. Enter a model ID manually "
            "or check the API base URL."
        )
    return tuple(sorted(model_ids))
