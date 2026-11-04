import os
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from app_i18n import DEFAULT_UI_LANGUAGE, set_ui_language

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("GPTSAPI_API_KEY", None)
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.pop("OPENROUTER_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)


@pytest.fixture(autouse=True)
def english_ui():
    """Each test starts and ends with the default English UI text."""
    set_ui_language(DEFAULT_UI_LANGUAGE)
    yield
    set_ui_language(DEFAULT_UI_LANGUAGE)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(["pytest"])


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]
