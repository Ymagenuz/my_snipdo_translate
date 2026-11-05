import os
import time
from pathlib import Path

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import ui_motion
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


@pytest.fixture(autouse=True)
def motion_off(monkeypatch):
    """Views jump and fades finish at once, whatever Windows is set to.

    Tests of the motion itself turn it on with the motion_on fixture.
    """
    monkeypatch.setattr(ui_motion, "animations_enabled", lambda: False)


@pytest.fixture
def motion_on(monkeypatch):
    monkeypatch.setattr(ui_motion, "animations_enabled", lambda: True)


@pytest.fixture
def wait_until(qapp):
    """Run the event loop, which drives animations, until condition() holds."""
    def wait(condition, timeout_ms=2000):
        deadline = time.monotonic() + timeout_ms / 1000
        while not condition():
            assert time.monotonic() < deadline, "timed out waiting"
            QTest.qWait(10)

    return wait


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(["pytest"])


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]
