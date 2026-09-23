from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCompleter, QDialog, QWidget

from api_providers import (
    DEFAULT_API_PROVIDER,
    DEEPSEEK_API_PROVIDER,
    OPENAI_COMPATIBLE_API_PROVIDER,
    ApiProviderConfig,
    api_provider_options,
    get_api_provider,
)
from app_settings import DEFAULT_SETTINGS, load_settings, save_settings_atomic


@pytest.fixture(scope="module")
def app_module():
    module_path = Path(__file__).resolve().parents[1] / "snipdo_translate.pyw"
    module_name = "_snipdo_translate_api_configuration_target"
    loader = importlib.machinery.SourceFileLoader(module_name, str(module_path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(module_name, None)


@pytest.fixture
def app(app_module, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("API settings tests must not use network or system credentials")

    monkeypatch.setattr(app_module, "OpenAI", forbidden)
    monkeypatch.setattr(app_module, "create_credential_store", forbidden)
    for provider in api_provider_options():
        monkeypatch.delenv(provider.environment_variable, raising=False)
    return app_module


@pytest.fixture
def dialog(app, qapp):
    dialog = app.SettingsDialog(
        DEFAULT_SETTINGS,
        api_key_configured=False,
        environment_key_active=False,
    )
    try:
        yield dialog
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()


@pytest.fixture
def requests(dialog, monkeypatch):
    calls = []
    monkeypatch.setattr(dialog._model_loader, "start", lambda *args: calls.append(args))
    return calls


def select_provider(dialog, provider_id):
    index = dialog.api_provider_combo.findData(provider_id)
    assert index >= 0
    dialog.api_provider_combo.setCurrentIndex(index)


def model_items(dialog):
    return [dialog.model_combo.itemText(index) for index in range(dialog.model_combo.count())]


def begin_fetch(dialog, requests):
    dialog.api_key_input.setText("test-only-key")
    dialog.fetch_models()
    assert requests
    return requests[-1][0]


def test_provider_drafts_survive_switches_and_persist_all_edits(dialog, tmp_path):
    dialog.base_url_input.setText("https://gateway.example.test/v2/")
    dialog.model_combo.setCurrentText("custom/gpts-model")
    dialog.vision_checkbox.setChecked(False)
    dialog.disable_thinking_checkbox.setChecked(True)
    dialog.api_key_input.setText("key-for-gptsapi-only")

    select_provider(dialog, OPENAI_COMPATIBLE_API_PROVIDER)
    assert dialog.api_key_input.text() == ""
    assert dialog.model_combo.currentText() == ""
    assert dialog.base_url_input.text() == "https://api.openai.com/v1"
    dialog.base_url_input.setText("http://localhost:8080/v1")
    dialog.model_combo.setCurrentText("local-vision-model")
    dialog.vision_checkbox.setChecked(True)

    select_provider(dialog, DEFAULT_API_PROVIDER)
    assert dialog.base_url_input.text() == "https://gateway.example.test/v2/"
    assert dialog.model_combo.currentText() == "custom/gpts-model"
    assert not dialog.vision_checkbox.isChecked()
    assert dialog.disable_thinking_checkbox.isChecked()

    select_provider(dialog, OPENAI_COMPATIBLE_API_PROVIDER)
    assert dialog.base_url_input.text() == "http://localhost:8080/v1"
    assert dialog.model_combo.currentText() == "local-vision-model"
    assert dialog.vision_checkbox.isChecked()
    candidate = dialog.candidate_settings()
    assert candidate.api_provider == OPENAI_COMPATIBLE_API_PROVIDER
    assert candidate.config_for(DEFAULT_API_PROVIDER) == ApiProviderConfig(
        "https://gateway.example.test/v2", "custom/gpts-model", False, True
    )
    assert candidate.config_for(OPENAI_COMPATIBLE_API_PROVIDER) == ApiProviderConfig(
        "http://localhost:8080/v1", "local-vision-model", True, False
    )
    settings_path = tmp_path / "settings.json"
    save_settings_atomic(settings_path, candidate)
    assert load_settings(settings_path) == candidate
    assert "key-for-gptsapi-only" not in settings_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", ""),
        ("url", "api.example.test/v1"),
        ("url", "https://api.example.test/v1?key=secret"),
        ("url", "https://user:secret@api.example.test/v1"),
        ("url", "https://api.example.test/v1#fragment"),
        ("model", ""),
        ("model", "   "),
        ("model", "invalid\x7fmodel"),
    ],
)
def test_invalid_configuration_keeps_settings_open_with_error(dialog, qapp, field, value):
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    if field == "url":
        dialog.base_url_input.setText(value)
    else:
        dialog.model_combo.setCurrentText(value)
    dialog.show()
    qapp.processEvents()

    dialog.accept()

    assert accepted == []
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.isVisible()
    assert "HTTP(S)" in dialog.status_label.text()
    assert "模型 ID" in dialog.status_label.text()
    assert "secret" not in dialog.status_label.text()


def test_fetch_uses_unsaved_url_options_and_typed_key(dialog, requests, app, monkeypatch):
    def forbidden_fallback(*_args):
        raise AssertionError("A typed key must take precedence over saved credentials")

    monkeypatch.setattr(app, "provider_api_key", forbidden_fallback)
    select_provider(dialog, OPENAI_COMPATIBLE_API_PROVIDER)
    dialog.base_url_input.setText(" https://draft.example.test/custom/v1/ ")
    dialog.model_combo.setCurrentText("")
    dialog.vision_checkbox.setChecked(True)
    dialog.disable_thinking_checkbox.setChecked(True)
    dialog.api_key_input.setText("  unsaved-key  ")

    dialog.fetch_models()

    assert len(requests) == 1
    _, provider_id, config, api_key = requests[0]
    assert provider_id == OPENAI_COMPATIBLE_API_PROVIDER
    assert config == ApiProviderConfig("https://draft.example.test/custom/v1", "", True, True)
    assert api_key == "unsaved-key"
    assert not dialog.fetch_models_button.isEnabled()
    dialog.fetch_models()
    assert len(requests) == 1


def test_fetch_reads_only_selected_providers_saved_key(app, qapp, monkeypatch):
    parent = QWidget()
    parent.app_settings = DEFAULT_SETTINGS
    reads = []
    created = []

    def gptsapi_read():
        raise AssertionError("The previous provider's credentials must not be read")

    parent.credential_store = SimpleNamespace(
        target_name=get_api_provider(DEFAULT_API_PROVIDER).credential_target,
        read=gptsapi_read,
    )

    def create_store(provider_id):
        created.append(provider_id)
        return SimpleNamespace(
            target_name=get_api_provider(provider_id).credential_target,
            read=lambda: reads.append(provider_id) or "selected-provider-key",
        )

    monkeypatch.setattr(app, "create_credential_store", create_store)
    dialog = app.SettingsDialog(
        DEFAULT_SETTINGS,
        api_key_configured=True,
        environment_key_active=False,
        parent=parent,
    )
    requests = []
    monkeypatch.setattr(dialog._model_loader, "start", lambda *args: requests.append(args))
    try:
        select_provider(dialog, OPENAI_COMPATIBLE_API_PROVIDER)
        dialog.fetch_models()

        assert created == [OPENAI_COMPATIBLE_API_PROVIDER]
        assert reads == [OPENAI_COMPATIBLE_API_PROVIDER]
        assert requests[0][1] == OPENAI_COMPATIBLE_API_PROVIDER
        assert requests[0][3] == "selected-provider-key"
    finally:
        dialog.reject()
        dialog.deleteLater()
        parent.deleteLater()
        qapp.processEvents()


def test_loaded_models_preserve_manual_choice_and_offer_contains_search(dialog, requests):
    dialog.model_combo.setCurrentText("my-manual-model")
    request_id = begin_fetch(dialog, requests)

    dialog._on_models_loaded(request_id, ("provider/alpha", "provider/beta-chat"), "")

    assert dialog.model_combo.isEditable()
    assert dialog.model_combo.currentText() == "my-manual-model"
    assert model_items(dialog) == ["my-manual-model", "provider/alpha", "provider/beta-chat"]
    completer = dialog.model_combo.completer()
    assert completer.completionMode() == QCompleter.CompletionMode.PopupCompletion
    assert completer.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
    assert completer.filterMode() == Qt.MatchFlag.MatchContains
    completer.setCompletionPrefix("BeTA")
    assert completer.completionCount() == 1
    assert completer.currentCompletion() == "provider/beta-chat"
    dialog.model_combo.setCurrentIndex(dialog.model_combo.findText("provider/beta-chat"))
    assert dialog.candidate_settings().config_for(DEFAULT_API_PROVIDER).model == "provider/beta-chat"
    assert dialog.fetch_models_button.isEnabled()


@pytest.mark.parametrize("change", ["provider", "url", "key"])
def test_old_model_results_are_ignored_after_request_context_changes(dialog, requests, change):
    request_id = begin_fetch(dialog, requests)
    if change == "provider":
        select_provider(dialog, DEEPSEEK_API_PROVIDER)
    elif change == "url":
        dialog.base_url_input.setText("https://different.example.test/v1")
    else:
        dialog.api_key_input.setText("replacement-key")
    manual_model = dialog.model_combo.currentText()
    status_before = dialog.model_status_label.text()

    dialog._on_models_loaded(request_id, ("stale-model",), "")

    assert dialog.model_combo.currentText() == manual_model
    assert "stale-model" not in model_items(dialog)
    assert dialog.model_status_label.text() == status_before
    assert dialog.fetch_models_button.isEnabled()


def test_discovery_error_keeps_manual_model_editable_and_allows_retry(dialog, requests):
    dialog.model_combo.setCurrentText("custom-unlisted-model")
    request_id = begin_fetch(dialog, requests)

    dialog._on_models_loaded(request_id, (), "Unable to fetch models; enter a model manually.")

    assert dialog.model_combo.currentText() == "custom-unlisted-model"
    assert dialog.model_combo.isEditable()
    assert "Unable to fetch" in dialog.model_status_label.text()
    assert dialog.fetch_models_button.isEnabled()
    dialog.fetch_models()
    assert len(requests) == 2


def test_changing_key_discards_previously_cached_models_but_keeps_current_choice(dialog, requests):
    request_id = begin_fetch(dialog, requests)
    dialog._on_models_loaded(request_id, ("current-choice", "old-key-only-model"), "")
    dialog.model_combo.setCurrentText("current-choice")
    assert "old-key-only-model" in model_items(dialog)

    dialog.api_key_input.setText("different-provider-key")

    assert model_items(dialog) == ["current-choice"]
    assert dialog.model_combo.currentText() == "current-choice"
    select_provider(dialog, DEEPSEEK_API_PROVIDER)
    select_provider(dialog, DEFAULT_API_PROVIDER)
    assert model_items(dialog) == ["current-choice"]
    assert "old-key-only-model" not in model_items(dialog)


def test_missing_key_prevents_discovery_without_blocking_manual_models(dialog, requests):
    dialog.model_combo.setCurrentText("manual-model")
    dialog.fetch_models()

    assert requests == []
    assert dialog.model_combo.currentText() == "manual-model"
    assert "API Key" in dialog.model_status_label.text()
    assert dialog.candidate_settings().config_for(DEFAULT_API_PROVIDER).model == "manual-model"


def test_model_loader_closes_temporary_client_before_emitting_results(app, qapp, monkeypatch):
    events = []
    api_client = SimpleNamespace(close=lambda: events.append("closed"))
    config = ApiProviderConfig("https://custom.example.test/v1", "", False)
    arguments = []
    monkeypatch.setattr(
        app, "create_api_client",
        lambda *args: arguments.append(args) or api_client,
    )
    monkeypatch.setattr(app, "fetch_available_models", lambda client: ("listed-model",))
    loader = app.ModelListLoader()
    loader.completed.connect(lambda *args: events.append(args))

    loader._load(7, OPENAI_COMPATIBLE_API_PROVIDER, config, "test-only-key")

    assert arguments == [("test-only-key", OPENAI_COMPATIBLE_API_PROVIDER, config)]
    assert events == ["closed", (7, ("listed-model",), "")]


def test_stopping_model_loader_during_request_suppresses_results_and_closes_client(app, qapp, monkeypatch):
    closed = []
    results = []
    client = SimpleNamespace(close=lambda: closed.append(True))
    loader = app.ModelListLoader()
    loader.completed.connect(lambda *args: results.append(args))
    monkeypatch.setattr(app, "create_api_client", lambda *_args: client)

    def fetch(_client):
        loader.stop()
        return ("late-model",)

    monkeypatch.setattr(app, "fetch_available_models", fetch)

    loader._load(8, DEFAULT_API_PROVIDER, DEFAULT_SETTINGS.config_for(DEFAULT_API_PROVIDER), "test-key")

    assert closed == [True]
    assert results == []


@pytest.mark.parametrize("failure_stage", ["create", "fetch", "close"])
def test_model_loader_never_emits_secret_exception_details(app, qapp, monkeypatch, failure_stage):
    closed = []
    results = []
    secret_detail = "sk-secret-key user-private-response https://private.example.test"

    def fail():
        raise RuntimeError(secret_detail)

    def close():
        closed.append(True)
        if failure_stage == "close":
            fail()

    def create(*_args):
        if failure_stage == "create":
            fail()
        return SimpleNamespace(close=close)

    def fetch(_client):
        if failure_stage == "fetch":
            fail()
        return ("listed-model",)

    monkeypatch.setattr(app, "create_api_client", create)
    monkeypatch.setattr(app, "fetch_available_models", fetch)
    loader = app.ModelListLoader()
    loader.completed.connect(lambda *args: results.append(args))

    loader._load(9, DEFAULT_API_PROVIDER, DEFAULT_SETTINGS.config_for(DEFAULT_API_PROVIDER), "test-key")

    assert len(results) == 1
    assert secret_detail not in str(results)
    if failure_stage == "create":
        assert closed == []
    else:
        assert closed == [True]
    if failure_stage in {"create", "fetch"}:
        assert results[0][1] == ()
        assert "模型 ID" in results[0][2]
    else:
        assert results[0] == (9, ("listed-model",), "")
