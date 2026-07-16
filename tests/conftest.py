import os
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("GPTSAPI_API_KEY", None)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(["pytest"])


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]
