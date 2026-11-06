import sys
import os

# A copied one-file EXE runs the installer without loading Qt, credentials,
# hooks or single-instance IPC. This must precede application imports.
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--apply-update":
    from app_update_install import run_apply_update
    raise SystemExit(run_apply_update(sys.argv[2]) if len(sys.argv) == 3 else 2)

import re
import json
import math
import warnings
import time
import ctypes
import base64
import mimetypes
import uuid
import threading
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote

from httpx import Limits
from openai import DefaultHttpxClient, OpenAI

from api_connection import (
    ConnectionWarmer,
    attach_connection_warmer,
    connection_warmer,
)
from api_providers import (
    DEFAULT_API_PROVIDER,
    OPENAI_COMPATIBLE_API_PROVIDER,
    ApiProviderConfig,
    ApiProviderSpec,
    api_provider_options,
    chat_completion_options,
    get_api_provider,
    provider_default_config,
)
from model_discovery import fetch_available_models

from app_cli import (
    AppRequest,
    CliError,
    PreparedRequest,
    delete_acknowledged_source,
    parse_cli,
    prepare_request,
)
from app_logging import AppEvent, PrivacyEventLogger, configure_app_logging
from app_changelog import (
    CHANGELOG_FILES,
    compose_changelog,
    load_changelog,
    render_changelog,
)
from app_update_ui import UpdateNotifications
from app_update_install import acknowledge_update_startup, consume_update_error
from app_updates import REPOSITORY_URL
from app_version import APP_VERSION
from app_i18n import (
    UI_LANGUAGES,
    language_name,
    messages,
    native_language_name,
    provider_name,
    set_ui_language,
    tr,
    ui_language,
)
from app_settings import (
    DEFAULT_SETTINGS,
    SETTINGS_FILE_NAME,
    AppSettings,
    SettingsDataError,
    ShortcutBinding,
    keyboard_shortcut,
    load_settings,
    mouse_shortcut,
    save_settings_atomic,
)
from app_paths import (
    AppPaths,
    HistoryDataError,
    ensure_app_directories,
    load_history,
    migrate_legacy_history,
    resolve_app_paths,
    save_history_atomic,
)
from credential_store import (
    WindowsCredentialStore,
    is_placeholder_api_key,
    resolve_api_key,
)
from windows_ipc import (
    IpcError,
    ReceiveResult,
    RejectionReason,
    SingleInstanceServer,
    decode_json_payload,
    encode_json_frame,
    send_request,
    validate_request_dict,
)
from windows_mouse_hook import WindowsMouseShortcutHook
from windows_selection import copy_keys_released, send_ctrl_c
from windows_startup import StartupRegistrationError, WindowsStartupRegistration
from latex_support import (
    LatexStreamRestorer,
    is_latex_text,
    latex_prose_for_language_detection,
    protect_latex_fragments,
)
from latex_renderer import (
    prepare_latex_math_for_document,
    render_latex_fragment_png,
    render_math_fragments_in_document,
    show_math_fragment_sources,
    warm_up_math_rendering,
)
from ui_controls import (
    DROPDOWN_STYLE,
    MENU_STYLE,
    TOOLTIP_STYLE,
    BusyPlaceholder,
    DropdownComboBox,
    ModernCheckBox,
    style_popup_list,
    use_modern_context_menu,
    use_modern_scroll_bar,
    use_modern_tooltips,
)
from ui_dialogs import BUTTON_STYLE, ask_text, show_warning
from ui_motion import SmoothScroller, fade_in, fade_opacity

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTextEdit,
    QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QHBoxLayout, QSystemTrayIcon, QMenu, QLineEdit,
    QComboBox, QDialog, QFormLayout,
    QDialogButtonBox, QCompleter, QListWidget, QStackedWidget,
    QTextBrowser, QGraphicsScene, QGraphicsPathItem
)
from PyQt6.QtGui import (
    QColor, QScreen, QTextCursor, QTextCharFormat,
    QTextBlockFormat, QTextFormat, QFont, QAction, QIcon, QImage,
    QKeySequence, QPainter, QPen, QPainterPath, QPixmap, QRegion, QTransform
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QThread, QObject, QTimer, QByteArray, QBuffer,
    QIODevice, QMimeData, QKeyCombination, QAbstractNativeEventFilter,
    QEvent, QLocale, QPoint, QPointF, QRectF, QSize
)

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
MODEL_NAME = get_api_provider(DEFAULT_API_PROVIDER).model
REQUEST_TIMEOUT_SECONDS = 15.0
# OpenRouter's edge closes idle connections after 120 seconds. Pooled
# connections expire just before that, and warm-up refreshes them sooner.
HTTP_KEEPALIVE_SECONDS = 110.0
CONNECTION_REFRESH_SECONDS = 90.0
CONNECTION_REFRESH_CHECK_MS = 15_000
CONNECTION_KEEP_WARM_SECONDS = 15 * 60.0
CONNECTION_WARMUP_TIMEOUT_SECONDS = 5.0
CONNECTION_WARMUP_WAIT_SECONDS = 2.0
LIVE_RENDER_INTERVAL_SECONDS = 0.12
MAX_HISTORY_ITEMS = 50
APP_ID = "SnipDoTranslate"
APP_DISPLAY_NAME = "SnipDo Translate"
IPC_UI_TIMEOUT_SECONDS = 4.0
SELECTION_KEY_RELEASE_TIMEOUT_SECONDS = 2.0
SELECTION_COPY_TIMEOUT_SECONDS = 1.0
NORMAL_WINDOW_WIDTH = 560
NORMAL_WINDOW_HEIGHT = 700

APP_PATHS: AppPaths | None = None
CREDENTIAL_STORE: WindowsCredentialStore | None = None
EVENT_LOG: PrivacyEventLogger | None = None
client = None


@dataclass(frozen=True)
class ApiRuntime:
    provider: ApiProviderSpec
    client: object | None


api_runtime = ApiRuntime(get_api_provider(DEFAULT_API_PROVIDER), None)


def record_event(event: AppEvent) -> None:
    """Best-effort fixed-event logging; never accepts caller-provided data."""
    if EVENT_LOG is None:
        return
    try:
        EVENT_LOG.event(event)
    except Exception:
        pass


def log(_discarded_message: object) -> None:
    """Compatibility sink for legacy debug calls.

    Older UI code still constructs diagnostic strings that may contain content,
    paths, or exception text.  They are intentionally discarded.  New runtime
    diagnostics must use ``record_event`` with an allow-listed ``AppEvent``.
    """
    return None


def activate_api_runtime(
    api_provider: str,
    api_client: object | None,
    config: ApiProviderConfig | None = None,
) -> None:
    global api_runtime, client

    api_runtime = ApiRuntime(get_api_provider(api_provider, config), api_client)
    # Keep the original module global as a compatibility alias for existing
    # integrations. Runtime calls use the immutable snapshot above.
    client = api_client


def create_api_client(
    api_key: str,
    api_provider: str = DEFAULT_API_PROVIDER,
    config: ApiProviderConfig | None = None,
):
    api_key = (api_key or "").strip()
    if is_placeholder_api_key(api_key):
        return None

    try:
        provider = get_api_provider(api_provider, config)
    except ValueError:
        return None

    http_client = None
    try:
        http_client = DefaultHttpxClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            limits=Limits(
                max_connections=1000,
                max_keepalive_connections=100,
                keepalive_expiry=HTTP_KEEPALIVE_SECONDS,
            ),
        )
        api_client = OpenAI(
            api_key=api_key,
            base_url=provider.base_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
            http_client=http_client,
        )
        attach_connection_warmer(
            api_client,
            ConnectionWarmer(
                http_client,
                provider.base_url,
                refresh_after=CONNECTION_REFRESH_SECONDS,
                keep_warm_for=CONNECTION_KEEP_WARM_SECONDS,
                timeout=CONNECTION_WARMUP_TIMEOUT_SECONDS,
            ),
        )
        return api_client
    except Exception:
        if http_client is not None:
            try:
                http_client.close()
            except Exception:
                pass
        return None


def configure_api_client(
    api_key: str,
    api_provider: str = DEFAULT_API_PROVIDER,
    config: ApiProviderConfig | None = None,
) -> bool:
    options = {"config": config} if config is not None else {}
    candidate = create_api_client(api_key, api_provider, **options)
    activate_api_runtime(api_provider, candidate, config)
    return candidate is not None


def create_credential_store(api_provider: str) -> WindowsCredentialStore:
    provider = get_api_provider(api_provider)
    return WindowsCredentialStore(target_name=provider.credential_target)


def provider_api_key(
    api_provider: str,
    store: WindowsCredentialStore | None,
) -> str:
    provider = get_api_provider(api_provider)
    environment_key = os.getenv(provider.environment_variable, "")
    if not is_placeholder_api_key(environment_key):
        return environment_key.strip()
    if store is None:
        return ""
    try:
        stored_key = store.read()
    except Exception:
        return ""
    return "" if is_placeholder_api_key(stored_key) else stored_key.strip()


def credential_store_for_window(window: object, api_provider: str):
    current_settings = getattr(window, "app_settings", None)
    current_store = getattr(window, "credential_store", None)
    expected_target = get_api_provider(api_provider).credential_target
    if (
        current_settings is not None
        and current_settings.api_provider == api_provider
        and current_store is not None
        and getattr(current_store, "target_name", None) == expected_target
    ):
        return current_store
    try:
        return create_credential_store(api_provider)
    except Exception:
        return None


def api_provider_for_window(window: object) -> ApiProviderSpec:
    settings = getattr(window, "app_settings", None)
    provider_id = getattr(settings, "api_provider", DEFAULT_API_PROVIDER)
    try:
        if isinstance(settings, AppSettings):
            return settings.resolved_provider()
        return get_api_provider(provider_id)
    except ValueError:
        return get_api_provider(DEFAULT_API_PROVIDER)


def initialize_credentials(
    paths: AppPaths,
    api_provider: str = DEFAULT_API_PROVIDER,
    config: ApiProviderConfig | None = None,
) -> WindowsCredentialStore | None:
    """Resolve a key without making a network request or writing plaintext."""
    global CREDENTIAL_STORE

    try:
        provider = get_api_provider(api_provider, config)
    except ValueError:
        provider = get_api_provider(DEFAULT_API_PROVIDER)
        api_provider = provider.provider_id
        config = None
    activate_api_runtime(api_provider, None, config)

    try:
        store = create_credential_store(api_provider)
        CREDENTIAL_STORE = store
        resolution = resolve_api_key(
            os.getenv(provider.environment_variable, ""),
            store,
            paths.legacy_dirs if api_provider == DEFAULT_API_PROVIDER else (),
        )
    except Exception:
        record_event(AppEvent.CREDENTIAL_UNAVAILABLE)
        return CREDENTIAL_STORE

    if not resolution.key or not configure_api_client(
        resolution.key,
        api_provider,
        **({"config": config} if config is not None else {}),
    ):
        record_event(AppEvent.CREDENTIAL_MISSING)
        return store

    record_event(
        AppEvent.CREDENTIAL_MIGRATED
        if resolution.migrated
        else AppEvent.CREDENTIAL_AVAILABLE
    )
    return store


# ===========================================


# ================= Windows 前台显示工具 =================
user32 = ctypes.windll.user32

SW_RESTORE = 9
SW_SHOW = 5

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
TRANSLATION_HOTKEY_ID = 0x5344
SHOW_WINDOW_HOTKEY_ID = 0x5345

user32.RegisterHotKey.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.c_uint,
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetClipboardSequenceNumber.restype = ctypes.c_ulong
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.BOOL,
]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND


def get_clipboard_sequence_number() -> int:
    try:
        return int(user32.GetClipboardSequenceNumber())
    except Exception:
        return 0


def win32_force_foreground(hwnd: int) -> bool:
    """
    在 Windows 下尽量把指定窗口恢复并前置到前台。
    仅依赖 Qt 的 show()/raise_()/activateWindow() 在 pythonw + 托盘 + 外部唤起场景下不够稳定，
    因此这里额外调用 Win32 API 强制显示窗口。
    """
    try:
        if not hwnd:
            return False

        user32.ShowWindow(
            hwnd,
            SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW,
        )

        foreground_hwnd = int(user32.GetForegroundWindow() or 0)
        target_thread_id = int(user32.GetWindowThreadProcessId(hwnd, None))
        foreground_thread_id = (
            int(user32.GetWindowThreadProcessId(foreground_hwnd, None))
            if foreground_hwnd
            else 0
        )
        attached = False
        if (
            foreground_thread_id
            and target_thread_id
            and foreground_thread_id != target_thread_id
        ):
            attached = bool(
                user32.AttachThreadInput(
                    target_thread_id,
                    foreground_thread_id,
                    True,
                )
            )

        try:
            user32.BringWindowToTop(hwnd)
            foregrounded = bool(user32.SetForegroundWindow(hwnd))
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(
                    target_thread_id,
                    foreground_thread_id,
                    False,
                )

        return bool(
            foregrounded or int(user32.GetForegroundWindow() or 0) == hwnd
        )
    except Exception as e:
        log(f"[Win32] force foreground error: {e}")
        return False


def create_settings_icon() -> QIcon:
    """Draw a gear at multiple resolutions without relying on a symbol font."""
    gear = QPainterPath()
    gear.addEllipse(QRectF(5, 5, 14, 14))
    tooth = QPainterPath()
    tooth.addRoundedRect(QRectF(9.5, 2, 5, 7), 1, 1)
    for angle in range(0, 360, 45):
        transform = QTransform().translate(12, 12).rotate(angle).translate(-12, -12)
        gear = gear.united(transform.map(tooth))
    hole = QPainterPath()
    hole.addEllipse(QRectF(8.5, 8.5, 7, 7))
    gear = gear.subtracted(hole)

    icon = QIcon()
    for size in (20, 40, 60):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(size / 24, size / 24)
        painter.fillPath(gear, QColor("#909399"))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def create_update_icon() -> QIcon:
    """Draw the blue download arrow the Settings button shows while an update waits."""
    arrow = QPainterPath()
    arrow.moveTo(12, 3)
    arrow.lineTo(12, 14.5)
    arrow.moveTo(7, 10)
    arrow.lineTo(12, 15)
    arrow.lineTo(17, 10)
    arrow.moveTo(4, 15)
    arrow.lineTo(4, 20)
    arrow.lineTo(20, 20)
    arrow.lineTo(20, 15)

    pen = QPen(QColor("#409EFF"))
    pen.setWidthF(2.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    icon = QIcon()
    for size in (20, 40, 60):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(size / 24, size / 24)
        painter.strokePath(arrow, pen)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def render_card_shadow(size: QSize, dpr: float) -> tuple[QPixmap, QPoint]:
    """Render the main card's soft shadow once, for the window to paint.

    The card used to carry this drop shadow as a graphics effect, which redrew
    and blurred the whole card on every repaint: each scroll frame took up to
    ten times longer. Returns the image and its offset from the card's corner.
    """
    card = QRectF(0, 0, size.width(), size.height())
    outline = QPainterPath()
    outline.addRoundedRect(card, 12, 12)
    face = QGraphicsPathItem(outline)
    face.setBrush(QColor("#FFFFFF"))
    face.setPen(QPen(Qt.PenStyle.NoPen))
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(20)
    shadow.setOffset(8, 4)
    shadow.setColor(QColor(0, 0, 0, 12))
    face.setGraphicsEffect(shadow)
    scene = QGraphicsScene()
    scene.addItem(face)

    bounds = shadow.boundingRectFor(card).toAlignedRect()
    image = QImage(
        math.ceil(bounds.width() * dpr),
        math.ceil(bounds.height() * dpr),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scene.render(
        painter, QRectF(0, 0, bounds.width(), bounds.height()), QRectF(bounds)
    )
    painter.end()
    return QPixmap.fromImage(image), bounds.topLeft()


def create_unread_badge_icon(base_icon: QIcon) -> QIcon:
    """Return a multi-resolution copy with a Windows-style unread dot."""
    if base_icon.isNull():
        return base_icon

    badged_icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        pixmap = base_icon.pixmap(size, size)
        if pixmap.isNull():
            continue

        device_pixel_ratio = max(1.0, float(pixmap.devicePixelRatio()))
        width = float(pixmap.width()) / device_pixel_ratio
        height = float(pixmap.height()) / device_pixel_ratio
        diameter = max(5.0, min(width, height) * 0.36)
        margin = max(0.5, min(width, height) * 0.025)
        badge_rect = QRectF(
            width - diameter - margin,
            margin,
            diameter,
            diameter,
        )

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        border_pen = QPen(QColor("#FFFFFF"))
        border_pen.setWidthF(max(1.0, diameter * 0.14))
        painter.setPen(border_pen)
        painter.setBrush(QColor("#E5484D"))
        painter.drawEllipse(badge_rect)
        painter.end()

        badged_icon.addPixmap(pixmap)

    return badged_icon if not badged_icon.isNull() else base_icon


# ================= 工具函数 =================
def normalize_newlines(text: str):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def bytes_to_data_url(image_bytes: bytes, mime_type: str = "image/png") -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def image_file_to_data_url(file_path: str) -> str:
    mime_type, _encoding = mimetypes.guess_type(file_path)
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/png"

    with open(file_path, "rb") as f:
        return bytes_to_data_url(f.read(), mime_type)


def clipboard_image_to_data_url() -> str:
    clipboard = QApplication.clipboard()
    image = clipboard.image()
    if image.isNull():
        raise RuntimeError(tr("error.clipboard_no_image"))

    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError(tr("error.clipboard_unreadable"))

    try:
        if not image.save(buffer, "PNG"):
            raise RuntimeError(tr("error.clipboard_not_png"))
    finally:
        buffer.close()

    return bytes_to_data_url(bytes(byte_array), "image/png")


def clone_clipboard_mime_data():
    clipboard = QApplication.clipboard()
    source = clipboard.mimeData()
    if source is None:
        return None

    clone = QMimeData()

    for mime_format in source.formats():
        clone.setData(mime_format, source.data(mime_format))

    if source.hasText():
        clone.setText(source.text())
    if source.hasHtml():
        clone.setHtml(source.html())
    if source.hasImage():
        clone.setImageData(source.imageData())
    if source.hasUrls():
        clone.setUrls(source.urls())
    if source.hasColor():
        clone.setColorData(source.colorData())

    return clone


class HtmlToMarkdownParser(HTMLParser):
    BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "div", "dl", "fieldset",
        "figcaption", "figure", "footer", "form", "header", "hr", "main",
        "nav", "p", "pre", "section",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignore_depth = 0
        self.list_stack = []
        self.link_stack = []
        self.heading_level = 0
        self.in_pre = False
        self.in_inline_code = False
        self.table = None
        self.current_row = None
        self.current_row_header_flags = None
        self.current_cell = None
        self.current_cell_is_header = False

    def result(self) -> str:
        text = "".join(self.parts)
        text = unescape(text)
        text = re.sub(r'[ \t]+\n', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def append(self, text: str):
        if not text:
            return
        if self.current_cell is not None:
            self.current_cell.append(text)
            return
        self.parts.append(text)

    def append_text(self, text: str):
        if not text or self.ignore_depth:
            return

        if not self.in_pre:
            text = re.sub(r'\s+', ' ', text)
            if self.current_cell is not None:
                if self.current_cell and not self.current_cell[-1].endswith((" ", "\n")):
                    text = text.lstrip()
                self.append(text)
                return

            previous = "".join(self.parts[-1:]) if self.parts else ""
            if previous.endswith((" ", "\n")):
                text = text.lstrip()

        self.append(text)

    def ensure_newline(self, count: int = 1):
        if self.current_cell is not None:
            return

        current = "".join(self.parts)
        trailing = len(current) - len(current.rstrip("\n"))
        if trailing < count:
            self.parts.append("\n" * (count - trailing))

    def ensure_block(self):
        self.ensure_newline(2)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr_map = dict(attrs or [])

        if tag in ("script", "style", "head", "meta", "noscript"):
            self.ignore_depth += 1
            return

        if self.ignore_depth:
            return

        if tag == "br":
            self.ensure_newline(1)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_level = int(tag[1])
            self.ensure_block()
            self.append("#" * self.heading_level + " ")
        elif tag in self.BLOCK_TAGS:
            if tag == "blockquote":
                self.ensure_block()
                self.append("> ")
            elif tag == "pre":
                self.ensure_block()
                self.append("```\n")
                self.in_pre = True
            else:
                self.ensure_block()
        elif tag in ("ul", "ol"):
            self.ensure_newline(1)
            self.list_stack.append({"type": tag, "index": 1})
        elif tag == "li":
            self.ensure_newline(1)
            indent = "  " * max(0, len(self.list_stack) - 1)
            if self.list_stack and self.list_stack[-1]["type"] == "ol":
                marker = f"{self.list_stack[-1]['index']}. "
                self.list_stack[-1]["index"] += 1
            else:
                marker = "- "
            self.append(indent + marker)
        elif tag in ("strong", "b"):
            self.append("**")
        elif tag in ("em", "i"):
            self.append("*")
        elif tag == "code" and not self.in_pre:
            self.in_inline_code = True
            self.append("`")
        elif tag == "a":
            href = (attr_map.get("href") or "").strip()
            self.link_stack.append(href)
            self.append("[")
        elif tag == "img":
            src = (attr_map.get("src") or "").strip()
            alt = (attr_map.get("alt") or "").strip()
            if src:
                self.append(f"![{alt}]({src})")
            elif alt:
                self.append(alt)
        elif tag == "table":
            self.ensure_block()
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.current_row = []
            self.current_row_header_flags = []
        elif tag in ("td", "th") and self.current_row is not None:
            self.current_cell = []
            self.current_cell_is_header = tag == "th"

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in ("script", "style", "head", "meta", "noscript"):
            self.ignore_depth = max(0, self.ignore_depth - 1)
            return

        if self.ignore_depth:
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_level = 0
            self.ensure_block()
        elif tag == "pre":
            if self.in_pre:
                self.ensure_newline(1)
                self.append("```")
                self.in_pre = False
            self.ensure_block()
        elif tag in self.BLOCK_TAGS:
            self.ensure_block()
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            self.ensure_newline(1)
        elif tag == "li":
            self.ensure_newline(1)
        elif tag in ("strong", "b"):
            self.append("**")
        elif tag in ("em", "i"):
            self.append("*")
        elif tag == "code" and self.in_inline_code:
            self.append("`")
            self.in_inline_code = False
        elif tag == "a":
            href = self.link_stack.pop() if self.link_stack else ""
            self.append(f"]({href})" if href else "]")
        elif tag in ("td", "th") and self.current_row is not None and self.current_cell is not None:
            cell_text = re.sub(r'\s+', ' ', "".join(self.current_cell)).strip()
            self.current_row.append(cell_text)
            self.current_row_header_flags.append(self.current_cell_is_header)
            self.current_cell = None
            self.current_cell_is_header = False
        elif tag == "tr" and self.table is not None and self.current_row is not None:
            if any(cell.strip() for cell in self.current_row):
                self.table.append((self.current_row, self.current_row_header_flags or []))
            self.current_row = None
            self.current_row_header_flags = None
        elif tag == "table" and self.table is not None:
            self.append(self.render_table(self.table))
            self.table = None
            self.ensure_block()

    def handle_data(self, data):
        self.append_text(data)

    @staticmethod
    def render_table(rows) -> str:
        if not rows:
            return ""

        max_cols = max(len(row) for row, _flags in rows)
        normalized_rows = []
        for row, _flags in rows:
            padded = list(row) + [""] * (max_cols - len(row))
            normalized_rows.append([cell.replace("|", "\\|") for cell in padded])

        header = normalized_rows[0]
        separator = ["---"] * max_cols
        body = normalized_rows[1:]

        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(separator) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in body)
        return "\n".join(lines)


def html_to_markdown(html_text: str) -> str:
    if not html_text:
        return ""

    parser = HtmlToMarkdownParser()
    try:
        parser.feed(html_text)
        parser.close()
        return parser.result()
    except Exception as e:
        log(f"[Format] html_to_markdown error: {e}")
        return ""


def is_markdown_structured_text(text: str) -> bool:
    if not text:
        return False

    return bool(re.search(
        r'(?m)^\s*(#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s+|```|\|.*\|)',
        text,
    ))


def is_structured_text(text: str) -> bool:
    return is_markdown_structured_text(text) or is_latex_text(text)


def markdown_format_instruction(text: str) -> str:
    has_markdown = is_markdown_structured_text(text)
    has_latex = is_latex_text(text)
    if not has_markdown and not has_latex:
        return ""

    requirements = ["格式要求："]
    if has_markdown:
        requirements.extend((
            "- 输入文本包含 Markdown/结构化格式；请保留标题层级、段落、列表、表格、链接和代码块结构。",
            "- 只翻译自然语言内容，不要翻译 Markdown 标记、URL、代码块、行内代码、变量名、函数名、文件路径和 HTML/XML 标签名。",
            "- 如果输入是表格，请保持相同的列数和行数；只翻译单元格里的自然语言。",
        ))
    if has_latex:
        requirements.extend((
            "- 输入文本包含 LaTeX；保持命令名、环境、花括号、换行及整体文档结构不变。",
            "- 只翻译正文、标题、图表标题以及文本命令参数中的自然语言；不要翻译公式、注释、引用键、标签、URL、文件路径或宏名称。",
            "- [[SNIPDO_LATEX_0000]] 这类标记是受保护的 LaTeX 片段占位符；必须逐字原样输出，每个标记恰好保留一次，不得改写、翻译或添加空格。",
            "- 直接输出原始 LaTeX，不要添加 Markdown 代码围栏。",
        ))
    requirements.append("- 直接输出保留格式后的译文，不要解释你做了什么。")
    return "\n".join(requirements)


def clipboard_mime_to_formatted_text(mime_data, sentinel: str = "") -> str:
    if mime_data is None:
        return ""

    plain_text = mime_data.text().strip() if mime_data.hasText() else ""
    html_text = mime_data.html() if mime_data.hasHtml() else ""

    if plain_text and plain_text != sentinel and is_latex_text(plain_text):
        return plain_text

    if html_text:
        markdown_text = html_to_markdown(html_text)
        if markdown_text and markdown_text != sentinel:
            if is_structured_text(markdown_text) or len(markdown_text) >= max(1, len(plain_text) // 2):
                return markdown_text.strip()

    if plain_text and plain_text != sentinel:
        return plain_text.strip()

    return ""


def normalize_input_text(raw_text: str):
    """
    处理来自 SnipDo 的原始文本，返回段落列表
    """
    if not raw_text:
        return []

    clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
    text_to_translate = (
        clean_text
        if is_latex_text(clean_text)
        else unquote(clean_text)
    )
    text_to_translate = normalize_newlines(text_to_translate)

    if is_structured_text(text_to_translate):
        return [text_to_translate.strip()]

    # 修复跨行断词：exam-
    #              ple -> example
    text = re.sub(r'-\s*\n\s*', '', text_to_translate)

    # 对“非段落换行”进行合并：如果不是句末结束且不是空行，则替换为空格
    text = re.sub(r'(?<![.!?。！？:：;；>”"\'])\n(?!\n)', ' ', text)

    original_paragraphs = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
    return original_paragraphs


def is_dictionary_mode(text: str) -> bool:
    """Recognize short lookup terms locally without an extra API request."""
    raw_text = (text or "").strip()
    text_clean = re.sub(r'\s+', ' ', raw_text)
    if not text_clean or "\n" in raw_text:
        return False
    if is_structured_text(raw_text):
        return False

    if re.search(r'[。！？；：.!?;:]', text_clean):
        return False

    cjk_chars = re.findall(r'[\u4e00-\u9fff]', text_clean)
    latin_words = re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", text_clean)

    if cjk_chars:
        if re.search(
            r'[\u6211\u4f60\u4ed6\u5979\u5b83\u4e86\u5417\u5462\u5427\u554a\u54e6\u5440]'
            r'|\u8fd9\u662f|\u90a3\u662f|\u4e0d\u662f|\u6ca1\u6709|\u5f88|\u592a',
            text_clean,
        ):
            return False
        return len(cjk_chars) <= 4 and len(text_clean) <= 8 and len(latin_words) <= 1

    if latin_words:
        if len(latin_words) == 1:
            return len(text_clean) <= 32
        return len(latin_words) <= 3 and len(text_clean) <= 28

    return len(text_clean) <= 10 and any(char.isalpha() for char in text_clean)


def resolve_auto_translation_mode(
    text: str,
    source_lang: str = "auto",
    target_lang: str = "default",
) -> str:
    """Resolve the translation direction locally without an LLM language pass."""
    if target_lang == "en":
        return "zh2en"
    if target_lang == "zh":
        return "en2zh"
    if target_lang != "default":
        return "auto"

    if source_lang == "zh":
        return "zh2en"
    if source_lang != "auto":
        return "en2zh"

    text_clean = (text or "").strip()
    if not text_clean:
        return "en2zh"
    text_clean = latex_prose_for_language_detection(text_clean)
    if not text_clean:
        return "en2zh"

    han_count = len(re.findall(r'[\u4e00-\u9fff]', text_clean))
    kana_or_hangul_count = len(
        re.findall(r'[\u3040-\u30ff\uac00-\ud7af]', text_clean)
    )
    latin_letter_count = len(re.findall(r'[A-Za-z]', text_clean))
    chinese_marker_count = len(re.findall(
        r'[的了是在我你他她它们这那不有和与及为就都而也很请将把被让]',
        text_clean,
    ))

    # Kana/Hangul normally means Japanese/Korean, but a small quoted term
    # should not override a predominantly Chinese paragraph.
    if kana_or_hangul_count and kana_or_hangul_count * 2 >= han_count:
        return "en2zh"
    if han_count and (
        not latin_letter_count
        or han_count * 4 + chinese_marker_count * 4 >= latin_letter_count
    ):
        return "zh2en"
    return "en2zh"


def target_label_for_mode(mode: str) -> str:
    if mode == "zh2en":
        return tr("main.label.english_translation")
    if mode == "en2zh":
        return tr("main.label.chinese_translation")
    return tr("main.label.auto_translation")


def translation_mode_for_target_language(target_lang: str) -> str:
    if target_lang == "en":
        return "zh2en"
    if target_lang == "zh":
        return "en2zh"
    return "auto"


def translation_target_language(mode: str, target_lang: str) -> str:
    """Language code of a translation's output; empty when it is unknown."""
    if mode == "zh2en":
        return "en"
    if mode == "en2zh":
        return "zh"
    return "" if target_lang == "default" else target_lang


# A few frequent function words per language; only scripts shared by several
# supported languages need them.
_LATIN_LANGUAGE_MARKERS = {
    "en": {"the", "and", "of", "to", "is", "are", "that", "with", "for", "this"},
    "fr": {"le", "les", "des", "est", "et", "une", "du", "pour", "dans", "pas"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "mit", "den"},
    "es": {"el", "los", "las", "y", "es", "por", "para", "con", "una", "del"},
    "it": {"il", "gli", "della", "che", "è", "per", "non", "sono", "una", "con"},
}


def detect_text_language(text: str) -> str:
    """Name the source language locally, so back-translation never sees the source."""
    if resolve_auto_translation_mode(text) == "zh2en":
        return "zh"
    sample = latex_prose_for_language_detection((text or "").strip())
    if re.search(r'[\u3040-\u30ff]', sample):
        return "ja"
    if re.search(r'[\uac00-\ud7af]', sample):
        return "ko"
    if re.search(r'[\u0400-\u04ff]', sample):
        return "ru"

    words = re.findall(r"[^\W\d_]+", sample.lower())
    scores = {
        code: sum(word in markers for word in words)
        for code, markers in _LATIN_LANGUAGE_MARKERS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > scores["en"] else "en"


def translation_label_for_target_language(mode: str, target_lang: str) -> str:
    if target_lang == "default":
        return target_label_for_mode(mode)
    if target_lang == "zh":
        return tr("main.label.chinese_translation")
    if target_lang == "en":
        return tr("main.label.english_translation")
    return tr(
        "main.label.language_translation",
        language=dictionary_language_display(target_lang, target=True).upper(),
    )


DICTIONARY_SOURCE_LANGUAGES = [
    ("auto", "Auto"),
    ("zh", "中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("ru", "Русский"),
    ("it", "Italiano"),
]

DICTIONARY_TARGET_LANGUAGES = [
    ("default", "默认"),
    ("zh", "中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("ru", "Русский"),
    ("it", "Italiano"),
]


def dictionary_language_label(code: str, target: bool = False) -> str:
    """Language name written into model prompts; never changes with the UI."""
    options = DICTIONARY_TARGET_LANGUAGES if target else DICTIONARY_SOURCE_LANGUAGES
    return next((label for value, label in options if value == code), "Auto")


def dictionary_language_display(code: str, target: bool = False) -> str:
    """Language name shown in the UI language."""
    options = DICTIONARY_TARGET_LANGUAGES if target else DICTIONARY_SOURCE_LANGUAGES
    return language_name(code if any(value == code for value, _label in options) else "auto")


# ================= 1. 统一后台翻译/查词线程 =================
# Workers report a stop with this error; it is never shown to the user.
CANCELLED_ERROR = "cancelled"


def close_stream(response) -> None:
    """End a streamed response now instead of when it is garbage collected."""
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception as e:
            log(f"[TranslateThread] close stream error: {e}")


class TranslationThread(QThread):
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, error_message

    def __init__(
        self,
        text: str,
        mode: str = "auto",
        dictionary_source_lang: str = "auto",
        dictionary_target_lang: str = "default",
    ):
        super().__init__()
        self.text = text
        self.mode = mode  # auto / en2zh / zh2en / dictionary
        self.dictionary_source_lang = dictionary_source_lang
        self.dictionary_target_lang = dictionary_target_lang
        self._stop_requested = False
        self.runtime = api_runtime
        self.latex_placeholders = {}

    def request_stop(self):
        self._stop_requested = True

    def build_prompt(self) -> str:
        raw_text_clean = self.text.strip()
        text_clean, self.latex_placeholders = protect_latex_fragments(
            raw_text_clean
        )

        if self.mode == "dictionary":
            source_lang = dictionary_language_label(self.dictionary_source_lang)
            target_lang = dictionary_language_label(self.dictionary_target_lang, target=True)
            return f"""
你是一个专业的多语言词典助手。请根据用户选择的语言设置，对下面的单词或短语进行释义和对应表达整理。

语言设置：
1. 原文语言：{source_lang}。如果是 Auto，请先自动识别原文语言。
2. 释义/对应表达语言：{target_lang}。如果是 默认，请按“中文词语优先给英文对应表达；非中文词语优先用简体中文释义”的默认中英查词习惯处理。

必须输出结构清晰、可直接渲染的 Markdown，并严格使用下面的层级和顺序：

# {{待查词条}}

> **语言**：{{原文语言}}
>
> **读音**：{{常见读音、音标或罗马化；确实不适用时写“不适用”}}

## 对应表达
1. **{{表达一}}** — {{简短说明}}
2. **{{表达二}}** — {{简短说明；没有第二项时省略}}

## 释义
- **{{词性或义项一}}**：{{解释}}
- **{{词性或义项二}}**：{{解释；没有第二项时省略}}

## 用法
- **常见搭配**：{{常见搭配或固定用法}}
- **语气与场景**：{{语气、正式程度和使用场景}}

## 例句
1. {{原文例句}}
   - **译文**：{{目标语言译文}}
2. {{第二个原文例句；没有时整项省略}}
   - **译文**：{{目标语言译文}}

要求：
- 一级标题只写待查词条；固定使用“对应表达、释义、用法、例句”四个二级标题，不得改成普通段落或连续编号
- 语言和读音必须放在标题后的引用块中；释义、用法必须使用列表；例句必须使用有序列表并把译文缩进到对应例句下
- 对应表达给出 1-3 项，例句给出 1-2 项；优先选择真实自然、常见的表达
- 如果是偏抽象概念，可给出意译而不是生硬直译
- 不要使用 Markdown 表格、代码块或额外的开场白、总结；直接从一级标题开始输出

待查内容：
{text_clean}
"""

        source_lang = dictionary_language_label(self.dictionary_source_lang)
        target_lang = dictionary_language_label(self.dictionary_target_lang, target=True)

        actual_mode = self.mode
        if actual_mode == "auto":
            actual_mode = resolve_auto_translation_mode(
                raw_text_clean,
                self.dictionary_source_lang,
                self.dictionary_target_lang,
            )

        if actual_mode == "auto":
            return f"""
你是一个专业的多语言翻译引擎。请根据用户选择的语言设置翻译下方文本。

语言设置：
1. 原文语言：{source_lang}。
2. 目标语言：{target_lang}。

规则：
1. 追求信达雅：根据目标语言的表达习惯自由调整句式、段落和语序，确保译文流畅、自然、专业。
2. 直接输出译文，不要说明识别到的语言，不要添加前缀或解释。

待翻译文本：
{text_clean}
"""

        if actual_mode == "zh2en":
            return f"""
你是一个专业的多语言翻译引擎。请将下方文本翻译成地道的英文。

规则：
1. 追求信达雅：根据英文母语者的表达习惯自由调整句式、段落和语序，确保译文流畅、自然、专业。
2. 直接输出译文，不要任何解释，不要加前缀。

待翻译文本：
{text_clean}
"""
        else:
            return f"""
你是一个专业的多语言翻译引擎。请将下方文本翻译成地道的简体中文。

规则：
1. 追求信达雅：根据中文表达习惯自由调整句式、段落和语序，确保译文流畅、自然、专业。
2. 直接输出译文，不要任何解释，不要加前缀。

待翻译文本：
{text_clean}
"""

    def run(self):
        warmer = connection_warmer(self.runtime.client)
        try:
            runtime = self.runtime
            if self._stop_requested:
                self.finished.emit(False, CANCELLED_ERROR)
                return
            if runtime.client is None:
                raise RuntimeError(
                    tr("error.no_api_key", provider=provider_name(runtime.provider.provider_id))
                )

            prompt = self.build_prompt()
            format_instruction = markdown_format_instruction(self.text)
            if format_instruction and self.mode != "dictionary":
                prompt = f"{format_instruction}\n\n{prompt}"
            log(f"[TranslateThread] start, mode={self.mode}, text={repr(self.text[:200])}")

            if warmer is not None:
                warmer.before_request(CONNECTION_WARMUP_WAIT_SECONDS)
            response = runtime.client.chat.completions.create(
                messages=[
                    {"role": "user", "content": prompt}
                ],
                stream=True,
                **chat_completion_options(runtime.provider, streaming=True),
            )
            stream_restorer = LatexStreamRestorer(
                self.latex_placeholders
            )

            try:
                for chunk in response:
                    if self._stop_requested:
                        log("[TranslateThread] cancelled")
                        self.finished.emit(False, CANCELLED_ERROR)
                        return

                    try:
                        delta = chunk.choices[0].delta
                        content = getattr(delta, "content", None)
                        if content:
                            restored_content = stream_restorer.feed(content)
                            if restored_content:
                                self.chunk_received.emit(restored_content)
                    except Exception:
                        continue
            finally:
                # A stopped stream would otherwise keep the model generating.
                close_stream(response)

            remaining_content = stream_restorer.finish()
            if remaining_content:
                self.chunk_received.emit(remaining_content)

            log("[TranslateThread] finished success")
            self.finished.emit(True, "")
        except Exception as e:
            log(f"[TranslateThread] error: {e}")
            self.finished.emit(False, str(e))
        finally:
            if warmer is not None:
                warmer.after_request()


class BackTranslationThread(TranslationThread):
    """Translate a finished translation back without showing it the source."""

    def __init__(self, text: str, language: str):
        super().__init__(text, "back")
        self.language = language

    def build_prompt(self) -> str:
        text_clean, self.latex_placeholders = protect_latex_fragments(
            self.text.strip()
        )
        return f"""
你是一个专业的回译引擎。用户要通过回译核对一段译文是否准确，请将下方文本忠实地翻译成{self.language}。

规则：
1. 忠实直译：逐句保留原意、语气、信息量和逻辑关系，不要润色、补充、删减或纠正。
2. 遇到含糊、生硬或可能有误的表达，按字面意思译出，不要猜测作者本意。
3. 直接输出译文，不要任何解释，不要加前缀。

待回译文本：
{text_clean}
"""


class AlignmentThread(QThread):
    finished = pyqtSignal(bool, object, str)  # success, match_data, error_message

    def __init__(
        self,
        source_text: str,
        target_text: str,
        selected_text: str,
        selected_sentence: str = "",
        selected_start_in_sentence: int = 0,
        selected_end_in_sentence: int = 0,
        left_context: str = "",
        right_context: str = "",
    ):
        super().__init__()
        self.source_text = source_text
        self.target_text = target_text
        self.selected_text = selected_text
        self.selected_sentence = selected_sentence
        self.selected_start_in_sentence = selected_start_in_sentence
        self.selected_end_in_sentence = selected_end_in_sentence
        self.left_context = left_context
        self.right_context = right_context
        self._stop_requested = False
        self.runtime = api_runtime

    def request_stop(self):
        self._stop_requested = True

    def build_prompt(self) -> str:
        payload = {
            "source_text": self.source_text,
            "target_text": self.target_text,
            "selected_text": self.selected_text,
            "selected_sentence": self.selected_sentence,
            "selected_start_in_sentence": self.selected_start_in_sentence,
            "selected_end_in_sentence": self.selected_end_in_sentence,
            "selected_left_context": self.left_context,
            "selected_right_context": self.right_context,
        }
        return f"""
你是一个双语文本对齐助手。用户会在 source_text 中选中一段文本，请在 target_text 中找出语义上最贴切对应的片段。

要求：
1. 先根据 selected_sentence 在 target_text 中找出对应的 target_sentence。selected_text 不足整句时，必须先完成这一步，再在 target_sentence 内找对应片段。
2. target_sentence 必须逐字复制自 target_text，表示 selected_sentence 的最贴切译文/原文句子。
3. selected_start_in_sentence 和 selected_end_in_sentence 是 selected_text 在 selected_sentence 中的字符下标，左闭右开；用它们判断 selected_text 在句内的具体位置。
4. text 必须是 target_sentence 内的最小对应片段。selected_text 是词或短语时，text 也应是词或短语，不要扩大成整句。
5. 如果 selected_text 是完整句子或从语义上接近完整句子，text 可以等于 target_sentence。
6. 如果 selected_text 原样或仅引号样式不同的形式出现在 target_sentence 中，必须返回 target_sentence 中的这个原样片段。
7. 如果 selected_text 是代码、函数名、变量名、字符串字面量、专有名词或被引号括起来的内容，优先返回 target_sentence 中同一个字面量，不要返回它的解释词。
8. 如果 target_sentence 中有多个相同的 text，请根据 selected_start_in_sentence、selected_left_context 和 selected_right_context 选择最贴近的那一次，并填写 occurrence_index。
9. target_left_context 和 target_right_context 必须从 target_sentence 中复制，分别是 text 目标片段左右两侧紧邻的少量字符，用于区分同词多次出现的位置。
10. 返回的 target_sentence 和 text 都必须逐字复制自 target_text，不能改写、翻译、补字或解释。
11. 不要返回 selected_text 的属性或解释。例如 selected_text 是 "Hello, World!" 时，如果 target_sentence 中也有 "Hello, World!"，返回它本身，不要返回“双引号”“字符串字面量”等解释。
12. 反向定位时也要保持粒度：selected_text 是“双引号”这种词语时，返回 source_text 中对应的最小词语或符号片段，不要扩大成整句。
13. 如果 selected_text 只是拉丁词的一部分，或者只是单个无语义标点，返回空字符串。
14. 如果 target_text 中没有合适片段，返回空字符串。
15. 只输出 JSON，不要输出 markdown。

JSON 格式：
{{"target_sentence":"target_text 中对应 selected_sentence 的精确句子","text":"target_sentence 中的精确片段","target_left_context":"紧邻左侧上下文","target_right_context":"紧邻右侧上下文","occurrence_index":1}}

输入：
{json.dumps(payload, ensure_ascii=False)}
"""

    def parse_response(self, content: str):
        content = (content or "").strip()
        if not content:
            return {"text": ""}

        try:
            data = json.loads(content)
        except Exception:
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if not match:
                return {"text": content.strip().strip('"')}
            try:
                data = json.loads(match.group(0))
            except Exception:
                return {"text": content.strip().strip('"')}

        if isinstance(data, dict):
            return {
                "text": str(data.get("text", "") or "").strip(),
                "target_sentence": str(data.get("target_sentence", "") or "").strip(),
                "target_left_context": str(data.get("target_left_context", "") or ""),
                "target_right_context": str(data.get("target_right_context", "") or ""),
                "occurrence_index": data.get("occurrence_index") or 0,
            }
        return {"text": ""}

    def run(self):
        try:
            runtime = self.runtime
            if self._stop_requested:
                self.finished.emit(False, {"text": ""}, CANCELLED_ERROR)
                return
            if runtime.client is None:
                raise RuntimeError(
                    tr("error.no_api_key", provider=provider_name(runtime.provider.provider_id))
                )

            log(f"[AlignmentThread] start, selected={repr(self.selected_text[:120])}")
            response = runtime.client.chat.completions.create(
                messages=[
                    {"role": "user", "content": self.build_prompt()}
                ],
                stream=False,
                **chat_completion_options(runtime.provider),
            )

            if self._stop_requested:
                log("[AlignmentThread] cancelled")
                self.finished.emit(False, {"text": ""}, CANCELLED_ERROR)
                return

            content = response.choices[0].message.content or ""
            match_data = self.parse_response(content)
            log(f"[AlignmentThread] matched={repr(match_data.get('text', '')[:120])}")
            self.finished.emit(True, match_data, "")
        except Exception as e:
            log(f"[AlignmentThread] error: {e}")
            self.finished.emit(False, {"text": ""}, str(e))


# ================= 2. OCR 线程 =================
class OcrThread(QThread):
    finished = pyqtSignal(bool, str, str)  # success, extracted_text, error_message

    def __init__(self, image_data_url: str):
        super().__init__()
        self.image_data_url = image_data_url
        self._stop_requested = False
        self.runtime = api_runtime

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            runtime = self.runtime
            if self._stop_requested:
                self.finished.emit(False, "", CANCELLED_ERROR)
                return
            if runtime.client is None:
                raise RuntimeError(
                    tr("error.no_api_key", provider=provider_name(runtime.provider.provider_id))
                )
            if not runtime.provider.supports_vision:
                raise RuntimeError(
                    tr("error.ocr_unsupported", provider=provider_name(runtime.provider.provider_id))
                )

            log("[OcrThread] start")
            response = runtime.client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "请对这张图片进行 OCR，只提取图片中可见的文字。\n"
                                    "要求：\n"
                                    "1. 保留原有段落、换行和阅读顺序。\n"
                                    "2. 不要翻译、不要解释、不要添加标题或 Markdown。\n"
                                    "3. 如果图片中没有可识别文字，只输出 NO_TEXT_FOUND。"
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": self.image_data_url},
                            },
                        ],
                    }
                ],
                stream=False,
                **chat_completion_options(runtime.provider),
            )

            if self._stop_requested:
                log("[OcrThread] cancelled")
                self.finished.emit(False, "", CANCELLED_ERROR)
                return

            content = response.choices[0].message.content or ""
            text = normalize_newlines(content).strip()
            if text.strip().upper() == "NO_TEXT_FOUND":
                raise RuntimeError(tr("error.no_text_found"))

            log(f"[OcrThread] finished success, text={repr(text[:200])}")
            self.finished.emit(True, text, "")
        except Exception as e:
            log(f"[OcrThread] error: {e}")
            self.finished.emit(False, "", str(e))


# ================= 3. 统一文本框：支持 Ctrl+Enter =================
class NoWheelComboBox(DropdownComboBox):
    def wheelEvent(self, event):
        event.ignore()


class InteractiveTextEdit(QTextEdit):
    submit_signal = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setStyleSheet("""
            QTextEdit {
                border: none;
                background-color: transparent;
                selection-background-color: #D7BDE2;
                selection-color: #303133;
            }
        """)
        # Its own style sheet, so the views' replacement style sheets keep it.
        use_modern_scroll_bar(self)
        use_modern_context_menu(self)
        self.smooth_scroller = SmoothScroller(self)

    def keyPressEvent(self, event):
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.submit_signal.emit()
            return

        super().keyPressEvent(event)


def qt_key_to_virtual_key(qt_key: int) -> int | None:
    """Best-effort Qt-to-Win32 key mapping for synthetic/non-native events."""
    if ord("0") <= qt_key <= ord("9") or ord("A") <= qt_key <= ord("Z"):
        return qt_key

    f1 = Qt.Key.Key_F1.value
    f24 = Qt.Key.Key_F24.value
    if f1 <= qt_key <= f24:
        return 0x70 + (qt_key - f1)

    return {
        Qt.Key.Key_Backspace.value: 0x08,
        Qt.Key.Key_Tab.value: 0x09,
        Qt.Key.Key_Return.value: 0x0D,
        Qt.Key.Key_Enter.value: 0x0D,
        Qt.Key.Key_Escape.value: 0x1B,
        Qt.Key.Key_Space.value: 0x20,
        Qt.Key.Key_PageUp.value: 0x21,
        Qt.Key.Key_PageDown.value: 0x22,
        Qt.Key.Key_End.value: 0x23,
        Qt.Key.Key_Home.value: 0x24,
        Qt.Key.Key_Left.value: 0x25,
        Qt.Key.Key_Up.value: 0x26,
        Qt.Key.Key_Right.value: 0x27,
        Qt.Key.Key_Down.value: 0x28,
        Qt.Key.Key_Insert.value: 0x2D,
        Qt.Key.Key_Delete.value: 0x2E,
    }.get(qt_key)


def keyboard_modifier_names(modifiers) -> tuple[str, ...]:
    names = []
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        names.append("ctrl")
    if modifiers & Qt.KeyboardModifier.AltModifier:
        names.append("alt")
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        names.append("shift")
    if modifiers & Qt.KeyboardModifier.MetaModifier:
        names.append("win")
    return tuple(names)


def keyboard_shortcut_display(qt_key: int, modifiers: tuple[str, ...]) -> str:
    try:
        key_name = QKeySequence(
            QKeyCombination(
                Qt.KeyboardModifier.NoModifier,
                Qt.Key(qt_key),
            )
        ).toString(QKeySequence.SequenceFormat.NativeText)
    except (TypeError, ValueError):
        key_name = ""

    if not key_name:
        return ""

    modifier_labels = {
        "ctrl": "Ctrl",
        "alt": "Alt",
        "shift": "Shift",
        "win": "Win",
    }
    return "+".join([*(modifier_labels[name] for name in modifiers), key_name])


def history_time_label(stamp: str, now: datetime | None = None) -> str:
    """"Today 04:00" for a History entry saved as "2026-11-05 04:00:07"."""
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return stamp or ""
    now = now or datetime.now()
    time_text = moment.strftime("%H:%M")
    if moment.date() == now.date():
        return tr("main.history.today", time=time_text)
    if moment.date() == (now - timedelta(days=1)).date():
        return tr("main.history.yesterday", time=time_text)
    locale = QLocale(ui_language().replace("-", "_"))
    if moment.year == now.year:
        day = locale.toString(moment.date(), tr("main.history.day_format"))
        return f"{day} {time_text}"
    return locale.toString(moment.date(), tr("main.history.year_format"))


def shortcut_label(binding: ShortcutBinding) -> str:
    """How a shortcut reads in the app: mouse buttons by name, not XButton1."""
    if binding.kind == "mouse":
        return tr(f"shortcut.mouse.{binding.mouse_button}")
    return binding.display


def shortcuts_conflict(
    first: ShortcutBinding | None,
    second: ShortcutBinding | None,
) -> bool:
    if first is None or second is None:
        return False
    if first.kind != second.kind:
        return False
    if first.kind == "mouse":
        return first.mouse_button == second.mouse_button
    return (
        first.virtual_key == second.virtual_key
        and first.modifiers == second.modifiers
    )


class ShortcutCaptureButton(QPushButton):
    capture_error = pyqtSignal(str)

    _MOUSE_BUTTONS = {
        Qt.MouseButton.BackButton: "xbutton1",
        Qt.MouseButton.ForwardButton: "xbutton2",
        Qt.MouseButton.MiddleButton: "middle",
    }

    def __init__(
        self, binding: ShortcutBinding | None, parent=None, *, allow_clear: bool = False
    ):
        super().__init__(parent)
        self._allow_clear = allow_clear
        self._capturing = False
        self.set_binding(binding)
        self.setMinimumWidth(180)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            tr("shortcut.tooltip.removable")
            if allow_clear else tr("shortcut.tooltip.required")
        )
        # A field with a key or mouse at its start and "Click to change" at its
        # end, so it reads as a place that records a shortcut, not a button.
        self.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                color: #303133;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 7px 12px 7px 36px;
                text-align: left;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                border-color: #C39BD3;
            }
            QPushButton:focus {
                border-color: #8E44AD;
                color: #8E44AD;
            }
        """)
        self.clicked.connect(self.begin_capture)
        self._refresh_text()

    @property
    def binding(self) -> ShortcutBinding | None:
        return self._binding

    def set_binding(self, binding: ShortcutBinding | None) -> None:
        if binding is None:
            if not self._allow_clear:
                raise SettingsDataError("this shortcut cannot be removed")
        elif not isinstance(binding, ShortcutBinding):
            raise SettingsDataError("shortcut must be a ShortcutBinding")
        self._binding = binding
        if not self._capturing:
            self._refresh_text()

    def _refresh_text(self) -> None:
        self.setText(
            shortcut_label(self._binding) if self._binding is not None else tr("shortcut.not_set")
        )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = self._capturing or self.hasFocus()
        pen = QPen(QColor("#8E44AD" if accent else "#909399"), 1.3)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        middle = self.height() / 2
        if self._binding is not None and self._binding.kind == "mouse" and not self._capturing:
            painter.drawRoundedRect(QRectF(15.5, middle - 7, 10, 14), 5, 5)
            painter.drawLine(QPointF(20.5, middle - 7), QPointF(20.5, middle - 2.5))
        else:
            painter.drawRoundedRect(QRectF(12.5, middle - 5.5, 17, 11), 2, 2)
            for x in (15.5, 19, 22.5, 26):
                painter.drawPoint(QPointF(x, middle - 2.2))
            painter.drawLine(QPointF(17, middle + 2), QPointF(25, middle + 2))
        if not self._capturing:
            font = QFont(self.font())
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor("#A8ABB2"))
            painter.drawText(
                self.rect().adjusted(0, 0, -12, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                tr("shortcut.click_to_change"),
            )
        painter.end()

    def begin_capture(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self.setText(
            tr("shortcut.capture.removable")
            if self._allow_clear else tr("shortcut.capture.required")
        )
        self.update()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grabKeyboard()
        self.grabMouse()

    def cancel_capture(self) -> None:
        if not self._capturing:
            return
        self._finish_capture()
        self._refresh_text()

    def _finish_capture(self) -> None:
        self._capturing = False
        if QWidget.keyboardGrabber() is self:
            self.releaseKeyboard()
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()

    def keyPressEvent(self, event):
        # Holding Esc after clearing must not reject the entire settings dialog.
        if event.key() == Qt.Key.Key_Escape and event.isAutoRepeat():
            event.accept()
            return
        if not self._capturing:
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_Escape:
            if self._allow_clear:
                self.set_binding(None)
            self.cancel_capture()
            event.accept()
            return

        modifier_keys = {
            Qt.Key.Key_Control,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Meta,
        }
        if event.key() in modifier_keys:
            event.accept()
            return

        modifiers = keyboard_modifier_names(event.modifiers())
        virtual_key = int(event.nativeVirtualKey()) or qt_key_to_virtual_key(event.key())
        display = keyboard_shortcut_display(event.key(), modifiers)
        if virtual_key is None or not display:
            self.capture_error.emit(tr("shortcut.error.unknown_key"))
            event.accept()
            return

        try:
            binding = keyboard_shortcut(virtual_key, modifiers, display)
        except (SettingsDataError, TypeError, ValueError):
            self.capture_error.emit(tr("shortcut.error.needs_modifier"))
            event.accept()
            return

        self._binding = binding
        self._finish_capture()
        self._refresh_text()
        event.accept()

    def mousePressEvent(self, event):
        if not self._capturing:
            super().mousePressEvent(event)
            return

        button_name = self._MOUSE_BUTTONS.get(event.button())
        if button_name is None:
            escape_hint = tr(
                "shortcut.hint.remove" if self._allow_clear else "shortcut.hint.cancel"
            )
            self.capture_error.emit(
                tr("shortcut.error.mouse_button", hint=escape_hint)
            )
            event.accept()
            return

        self._binding = mouse_shortcut(button_name)
        self._finish_capture()
        self._refresh_text()
        event.accept()


class ModelListLoader(QObject):
    completed = pyqtSignal(int, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stopped = threading.Event()

    def start(self, request_id, provider_id, config, api_key):
        threading.Thread(
            target=self._load,
            args=(request_id, provider_id, config, api_key),
            daemon=True,
            name="api-model-list",
        ).start()

    def stop(self):
        self._stopped.set()

    def _load(self, request_id, provider_id, config, api_key):
        api_client = None
        models = ()
        error = ""
        try:
            api_client = create_api_client(api_key, provider_id, config)
            if api_client is None:
                raise ValueError("client unavailable")
            models = fetch_available_models(api_client)
        except Exception:
            # Never display response bodies or credentials from service errors.
            error = tr("settings.api.model.fetch_failed")
        finally:
            if api_client is not None:
                try:
                    api_client.close()
                except Exception:
                    pass
        if not self._stopped.is_set():
            try:
                self.completed.emit(request_id, models, error)
            except RuntimeError:
                pass  # The dialog may have been destroyed during the request.


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: AppSettings,
        *,
        api_key_configured: bool,
        environment_key_active: bool,
        startup_enabled: bool = False,
        startup_available: bool = True,
        updates=None,
        page: str = "",
        changelog: str = "",
        app_icon=None,
        parent=None,
    ):
        super().__init__(parent)
        self._updates = updates
        # Set when the user picks Update and restart; installed after saving.
        self.requested_update = None
        self._initial_api_provider = settings.api_provider
        self._api_key_configured = api_key_configured
        self._environment_key_active = environment_key_active
        self._initial_settings = settings
        self._drafts = {}
        self._shown_provider = settings.api_provider
        self._model_lists = {}
        self._model_request_id = 0
        self._model_request_pending = False
        self._model_loader = ModelListLoader(self)
        self._model_loader.completed.connect(
            self._on_models_loaded, Qt.ConnectionType.QueuedConnection
        )
        self.setWindowTitle(tr("settings.window_title"))
        self.setModal(True)
        self.setMinimumWidth(720)
        self.setStyleSheet("""
            QDialog {
                background-color: #F5F7FA;
            }
            QListWidget {
                background-color: transparent;
                border: none;
                border-right: 1px solid #E4E7ED;
                outline: 0;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
            }
            QListWidget::item {
                color: #606266;
                padding: 8px 12px;
                margin: 1px 10px 1px 0;
                border-radius: 6px;
            }
            QListWidget::item:hover {
                background-color: #EBEEF5;
            }
            QListWidget::item:selected {
                background-color: #EDE7F6;
                color: #8E44AD;
            }
            QLabel {
                color: #606266;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
            }
            QCheckBox {
                color: #303133;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
                spacing: 8px;
            }
            QLineEdit, QComboBox {
                background-color: #FFFFFF;
                color: #303133;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 7px 10px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
                selection-background-color: #D7BDE2;
                selection-color: #303133;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #8E44AD;
            }
        """ + BUTTON_STYLE + TOOLTIP_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title = QLabel(tr("settings.title", version=APP_VERSION))
        title.setStyleSheet("color: #303133; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        # Each topic gets its own page so new options do not lengthen one form.
        body = QHBoxLayout()
        body.setSpacing(18)
        self.page_list = QListWidget()
        self.page_list.setFixedWidth(132)
        self.pages = QStackedWidget()
        self.page_list.currentRowChanged.connect(self._show_page)
        body.addWidget(self.page_list)
        body.addWidget(self.pages, 1)
        layout.addLayout(body, 1)

        form = self._add_page(
            tr("settings.general"),
            tr("settings.general.hint"),
        )
        # First on the first page, so anyone can find it in any language.
        self.language_combo = NoWheelComboBox()
        for code, name in UI_LANGUAGES:
            self.language_combo.addItem(name, code)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(settings.ui_language))
        )
        form.addRow(tr("settings.language.row"), self.language_combo)
        language_note = QLabel(tr("settings.language.note"))
        language_note.setWordWrap(True)
        language_note.setStyleSheet("color: #909399; font-size: 12px;")
        form.addRow("", language_note)

        self.chk_enabled = ModernCheckBox(tr("settings.enabled.check"))
        self.chk_enabled.setChecked(settings.enabled)
        form.addRow(tr("settings.enabled.row"), self.chk_enabled)

        self.chk_startup = ModernCheckBox(tr("settings.startup.check"))
        self.chk_startup.setChecked(startup_enabled)
        self.chk_startup.setEnabled(startup_available)
        self.chk_startup.setToolTip(
            tr("settings.startup.tooltip")
            if startup_available else tr("settings.startup.unavailable")
        )
        form.addRow(tr("settings.startup.row"), self.chk_startup)
        if not startup_available:
            startup_notice = QLabel(tr("settings.startup.unavailable"))
            startup_notice.setWordWrap(True)
            form.addRow("", startup_notice)

        self.chk_completion_notifications = ModernCheckBox(
            tr("settings.notifications.check")
        )
        self.chk_completion_notifications.setChecked(
            settings.completion_notifications
        )
        form.addRow(
            tr("settings.notifications.row"), self.chk_completion_notifications
        )
        notification_note = QLabel(tr("settings.notifications.note"))
        notification_note.setWordWrap(True)
        notification_note.setStyleSheet("color: #909399; font-size: 12px;")
        form.addRow("", notification_note)

        form = self._add_page(
            tr("settings.shortcuts"),
            tr("settings.shortcuts.hint"),
        )
        self.shortcut_button = ShortcutCaptureButton(settings.shortcut)
        self.shortcut_button.capture_error.connect(self._show_capture_error)
        form.addRow(tr("settings.shortcut.translate.row"), self.shortcut_button)

        self.show_window_shortcut_button = ShortcutCaptureButton(
            settings.show_window_shortcut, allow_clear=True
        )
        self.show_window_shortcut_button.capture_error.connect(
            self._show_capture_error
        )
        form.addRow(
            tr("settings.shortcut.show_window.row"), self.show_window_shortcut_button
        )
        self.shortcut_status_label = QLabel()
        self.shortcut_status_label.setWordWrap(True)
        self.shortcut_status_label.setStyleSheet("color: #E6A23C; font-size: 12px;")
        form.addRow("", self.shortcut_status_label)

        form = self._add_page(
            tr("settings.translation"),
            tr("settings.translation.hint"),
        )
        self.chk_back_translation = ModernCheckBox(tr("settings.back_translation.check"))
        self.chk_back_translation.setChecked(settings.back_translation)
        form.addRow(tr("settings.back_translation.row"), self.chk_back_translation)
        back_translation_note = QLabel(tr("settings.back_translation.note"))
        back_translation_note.setWordWrap(True)
        back_translation_note.setStyleSheet("color: #909399; font-size: 12px;")
        form.addRow("", back_translation_note)

        form = self._add_page(
            tr("settings.api"),
            tr("settings.api.hint"),
        )
        self._api_page = self.pages.count() - 1
        self.api_provider_combo = NoWheelComboBox()
        for provider in api_provider_options():
            self.api_provider_combo.addItem(
                provider_name(provider.provider_id),
                provider.provider_id,
            )
        provider_index = self.api_provider_combo.findData(settings.api_provider)
        self.api_provider_combo.setCurrentIndex(max(0, provider_index))
        form.addRow(tr("settings.api.provider.row"), self.api_provider_combo)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText(tr("settings.api.key.placeholder"))
        self.api_key_input.setClearButtonEnabled(True)
        form.addRow(tr("settings.api.key.row"), self.api_key_input)

        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("https://api.example.com/v1")
        self.base_url_input.setStyleSheet("""
            QLineEdit:read-only {
                background-color: #F2F3F5;
                color: #606266;
            }
        """)
        form.addRow(tr("settings.api.base_url.row"), self.base_url_input)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self.model_combo = NoWheelComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(18)
        self.model_combo.lineEdit().setPlaceholderText(
            tr("settings.api.model.placeholder")
        )
        self.model_combo.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.model_combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.model_combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.model_combo.completer().setMaxVisibleItems(12)
        style_popup_list(self.model_combo.completer().popup())
        self.fetch_models_button = QPushButton(tr("settings.api.model.fetch"))
        self.fetch_models_button.setAutoDefault(False)
        self.fetch_models_button.clicked.connect(self.fetch_models)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.fetch_models_button)
        form.addRow(tr("settings.api.model.row"), model_row)
        # As tall as the model box it sits beside.
        self.model_combo.ensurePolished()
        self.fetch_models_button.ensurePolished()
        self.fetch_models_button.setMinimumHeight(self.model_combo.sizeHint().height())

        self.vision_checkbox = ModernCheckBox(tr("settings.api.vision.check"))
        form.addRow(tr("settings.api.vision.row"), self.vision_checkbox)
        self.disable_thinking_checkbox = ModernCheckBox(tr("settings.api.thinking.check"))
        form.addRow(tr("settings.api.thinking.row"), self.disable_thinking_checkbox)

        self.model_status_label = QLabel(tr("settings.api.model.status"))
        self.model_status_label.setWordWrap(True)
        self.model_status_label.setStyleSheet("color: #909399; font-size: 12px;")
        form.addRow("", self.model_status_label)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #909399; font-size: 12px;")
        self.api_provider_combo.currentIndexChanged.connect(
            self._on_api_provider_changed
        )
        self.base_url_input.textChanged.connect(self._invalidate_model_list)
        self.api_key_input.textChanged.connect(self._on_api_key_changed)
        self.vision_checkbox.toggled.connect(self._refresh_api_key_status)
        self._load_provider_fields()
        form.addRow("", self.status_label)

        self._build_about_page(updates, changelog, app_icon)

        self.page_list.setCurrentRow(self._about_page if page == "about" else 0)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.StandardButton.Save).setText(
            tr("settings.save")
        )
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText(
            tr("settings.cancel")
        )
        layout.addWidget(button_box)
        use_modern_tooltips(self)
        for field in (self.api_key_input, self.base_url_input, self.model_combo.lineEdit()):
            use_modern_context_menu(field)

    def _build_about_page(self, updates, changelog: str, app_icon) -> None:
        # The changelog at the bottom takes the free space instead of a stretch.
        form = self._add_page(
            tr("settings.about"),
            tr("settings.about.hint", app=APP_DISPLAY_NAME),
            stretch=False,
        )
        self._about_page = self.pages.count() - 1

        card = QFrame()
        card.setObjectName("aboutCard")
        # The main window's background rule reaches every child widget; as the
        # nearer ancestor, the card keeps its text on plain white.
        card.setStyleSheet("""
            QFrame#aboutCard {
                background-color: #FFFFFF;
                border: 1px solid #E4E7ED;
                border-radius: 8px;
            }
            QFrame#aboutCard > QLabel {
                background-color: transparent;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        identity = QHBoxLayout()
        identity.setSpacing(12)
        if app_icon is not None and not app_icon.isNull():
            icon_label = QLabel()
            icon_label.setPixmap(app_icon.pixmap(44, 44))
            icon_label.setFixedSize(44, 44)
            identity.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        names = QVBoxLayout()
        names.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        name_label = QLabel(APP_DISPLAY_NAME)
        name_label.setStyleSheet("color: #303133; font-size: 16px; font-weight: 700;")
        title_row.addWidget(name_label)
        self.version_label = QLabel(tr("settings.about.version", version=APP_VERSION))
        self.version_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.version_label.setStyleSheet("""
            color: #8E44AD;
            background-color: #F3ECF8;
            border-radius: 9px;
            padding: 1px 8px;
            font-size: 12px;
            font-weight: 600;
        """)
        title_row.addWidget(self.version_label, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        names.addLayout(title_row)
        tagline = QLabel(tr("settings.about.tagline"))
        tagline.setWordWrap(True)
        tagline.setStyleSheet("color: #909399; font-size: 12px;")
        names.addWidget(tagline)
        identity.addLayout(names, 1)
        project_link = QLabel(
            f'<a href="{REPOSITORY_URL}" style="color: #8E44AD; '
            'text-decoration: none;">GitHub ↗</a>'
        )
        project_link.setTextFormat(Qt.TextFormat.RichText)
        project_link.setOpenExternalLinks(True)
        project_link.setToolTip(REPOSITORY_URL)
        identity.addWidget(project_link, 0, Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(identity)

        if updates is not None:
            divider = QFrame()
            divider.setFixedHeight(1)
            divider.setStyleSheet("background-color: #EBEEF5; border: none;")
            card_layout.addWidget(divider)
            status_row = QHBoxLayout()
            status_row.setSpacing(8)
            self.update_status_dot = QLabel()
            self.update_status_dot.setFixedSize(10, 10)
            status_row.addWidget(self.update_status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
            self.update_status_label = QLabel()
            self.update_status_label.setWordWrap(True)
            self.update_status_label.setStyleSheet("color: #303133; font-size: 13px;")
            status_row.addWidget(self.update_status_label, 1)
            card_layout.addLayout(status_row)

            update_buttons = QHBoxLayout()
            update_buttons.setSpacing(8)
            self.install_update_button = QPushButton(tr("update.button.install"))
            self.install_update_button.setStyleSheet("""
                QPushButton {
                    background-color: #8E44AD;
                    color: #FFFFFF;
                    border: 1px solid #8E44AD;
                    border-radius: 6px;
                    padding: 6px 16px;
                    font-size: 13px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #9B59B6;
                    border-color: #9B59B6;
                }
                QPushButton:pressed {
                    background-color: #7D3C98;
                    border-color: #7D3C98;
                }
            """)
            self.install_update_button.clicked.connect(self._install_update)
            self.release_notes_button = QPushButton(tr("update.button.release_notes"))
            self.release_notes_button.clicked.connect(self._open_release_notes)
            self.check_updates_button = QPushButton(tr("update.button.check"))
            self.check_updates_button.clicked.connect(
                lambda _checked=False: updates.check_manually()
            )
            secondary_style = """
                QPushButton {
                    background-color: #FFFFFF;
                    color: #606266;
                    border: 1px solid #DCDFE6;
                    border-radius: 6px;
                    padding: 6px 14px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    color: #8E44AD;
                    background-color: #F4ECF7;
                    border-color: #D7BDE2;
                }
                QPushButton:disabled {
                    color: #C0C4CC;
                    background-color: #FFFFFF;
                    border-color: #EBEEF5;
                }
            """
            self.release_notes_button.setStyleSheet(secondary_style)
            self.check_updates_button.setStyleSheet(secondary_style)
            for button in (
                self.install_update_button,
                self.release_notes_button,
                self.check_updates_button,
            ):
                button.setAutoDefault(False)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                update_buttons.addWidget(button)
            update_buttons.addStretch(1)
            card_layout.addLayout(update_buttons)
            self.update_note_label = QLabel()
            self.update_note_label.setWordWrap(True)
            self.update_note_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.update_note_label.setStyleSheet("color: #E6A23C; font-size: 12px;")
            self.update_note_label.hide()
            card_layout.addWidget(self.update_note_label)
        form.addRow(card)

        about_layout = self.pages.widget(self._about_page).layout()
        changelog_heading = QLabel(tr("settings.about.changelog"))
        changelog_heading.setStyleSheet(
            "color: #303133; font-size: 14px; font-weight: 700; margin-top: 10px;"
        )
        about_layout.addWidget(changelog_heading)
        self.changelog_view = QTextBrowser()
        self.changelog_view.setOpenExternalLinks(True)
        self.changelog_view.setMinimumHeight(200)
        self.changelog_view.setStyleSheet("""
            QTextBrowser {
                background-color: #FFFFFF;
                border: 1px solid #E4E7ED;
                border-radius: 8px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
                selection-background-color: #D7BDE2;
                selection-color: #303133;
            }
        """)
        use_modern_scroll_bar(self.changelog_view)
        use_modern_context_menu(self.changelog_view)
        SmoothScroller(self.changelog_view)
        about_layout.addWidget(self.changelog_view, 1)
        self._changelog = changelog
        self._changelog_release = None
        self._show_changelog()
        if updates is not None:
            updates.changed.connect(self._refresh_update_section)
            self._refresh_update_section()

    def _add_page(self, name: str, description: str, *, stretch: bool = True) -> QFormLayout:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(8)
        heading = QLabel(name)
        heading.setStyleSheet("color: #303133; font-size: 15px; font-weight: 700;")
        page_layout.addWidget(heading)
        hint = QLabel(description)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #909399; font-size: 12px;")
        page_layout.addWidget(hint)

        form = QFormLayout()
        form.setContentsMargins(0, 10, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        page_layout.addLayout(form)
        if stretch:
            page_layout.addStretch(1)

        self.pages.addWidget(page)
        self.page_list.addItem(name)
        return form

    def _show_page(self, row: int) -> None:
        self.pages.setCurrentIndex(row)
        fade_in(self.pages.currentWidget())

    def _show_capture_error(self, message: str) -> None:
        self.shortcut_status_label.setText(message)

    def _refresh_update_section(self) -> None:
        updates = self._updates
        release = updates.available_release
        if updates.is_installing:
            text, color = tr("update.about.installing"), "#E6A23C"
        elif updates.is_checking:
            text, color = tr("update.about.checking"), "#E6A23C"
        elif release is not None:
            text = tr("update.about.available", version=release.version, current=APP_VERSION)
            color = "#409EFF"
        elif updates.last_result == "current":
            text, color = tr("update.about.current"), "#67C23A"
        elif updates.last_result == "failed":
            text, color = tr("update.check_failed"), "#F56C6C"
        else:
            text, color = tr("update.about.automatic"), "#C0C4CC"
        self.update_status_label.setText(text)
        self.update_status_dot.setStyleSheet(
            f"background-color: {color}; border-radius: 5px;"
        )
        self.install_update_button.setVisible(
            release is not None and not updates.is_installing
        )
        self.release_notes_button.setVisible(release is not None)
        self.check_updates_button.setEnabled(
            not updates.is_checking and not updates.is_installing
        )
        if release != self._changelog_release:
            self._show_changelog()

    def _show_changelog(self) -> None:
        release = self._updates.available_release if self._updates is not None else None
        self._changelog_release = release
        markdown = compose_changelog(self._changelog, release)
        if not markdown:
            self.changelog_view.setPlainText(tr("settings.about.changelog.missing"))
            return
        render_changelog(
            self.changelog_view.document(),
            markdown,
            installed_version=APP_VERSION,
            available_version=release.version if release is not None else "",
        )
        self.changelog_view.moveCursor(QTextCursor.MoveOperation.Start)

    def _install_update(self) -> None:
        release = self._updates.available_release
        if release is None or self._updates.is_installing:
            return
        # Saves like the Save button, so changes made first aren't lost.
        self.requested_update = release
        self.accept()

    def _open_release_notes(self) -> None:
        release = self._updates.available_release
        if release is None:
            return
        opened = self._updates.open_release_page(release)
        self.update_note_label.setText(
            "" if opened else tr("update.browser_failed", url=release.url)
        )
        self.update_note_label.setVisible(not opened)

    def _show_api_error(self, message: str) -> None:
        self.page_list.setCurrentRow(self._api_page)
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #E6A23C; font-size: 12px;")

    def selected_api_provider(self) -> str:
        provider_id = self.api_provider_combo.currentData()
        return str(provider_id or DEFAULT_API_PROVIDER)

    def _on_api_provider_changed(self, _index: int) -> None:
        self._drafts[self._shown_provider] = self._field_values()
        self._shown_provider = self.selected_api_provider()
        self.api_key_input.clear()
        self._invalidate_model_list()
        self._load_provider_fields()

    def _field_values(self):
        return (
            self.base_url_input.text(),
            self.model_combo.currentText(),
            self.vision_checkbox.isChecked(),
            self.disable_thinking_checkbox.isChecked(),
        )

    def _load_provider_fields(self):
        provider_id = self.selected_api_provider()
        custom_provider = provider_id == OPENAI_COMPATIBLE_API_PROVIDER
        self.base_url_input.setReadOnly(not custom_provider)
        self.base_url_input.setToolTip(
            tr("settings.api.base_url.custom_tooltip")
            if custom_provider else
            tr(
                "settings.api.base_url.fixed_tooltip",
                provider=provider_name(OPENAI_COMPATIBLE_API_PROVIDER),
            )
        )
        config = self._initial_settings.config_for(provider_id)
        values = self._drafts.get(provider_id, (
            config.base_url, config.model,
            config.supports_vision, config.disable_thinking,
        ))
        self.base_url_input.setText(values[0])
        self.vision_checkbox.setChecked(values[2])
        self.disable_thinking_checkbox.setChecked(values[3])
        self._populate_models(values[1])
        self._refresh_api_key_status()

    def _populate_models(self, selected_model):
        models = self._model_lists.get(
            (self.selected_api_provider(), self.base_url_input.text().strip().rstrip("/")),
            (),
        )
        self.model_combo.clear()
        if selected_model and selected_model not in models:
            self.model_combo.addItem(selected_model)
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(selected_model)

    def _invalidate_model_list(self, *_args):
        self._model_request_id += 1
        self._populate_models(self.model_combo.currentText())
        self.model_status_label.setText(tr("settings.api.model.status"))

    def _on_api_key_changed(self, *_args):
        self._model_lists.clear()
        self._invalidate_model_list()

    def fetch_models(self):
        if self._model_request_pending:
            return
        try:
            config = ApiProviderConfig(*self._field_values())
        except ValueError:
            self.model_status_label.setText(tr("settings.api.model.invalid_url"))
            return
        provider_id = self.selected_api_provider()
        api_key = self.api_key()
        if not api_key:
            api_key = provider_api_key(
                provider_id, credential_store_for_window(self.parent(), provider_id)
            )
        if is_placeholder_api_key(api_key):
            self.model_status_label.setText(tr("settings.api.model.needs_key"))
            return
        self._model_request_id += 1
        self._model_request_pending = True
        self.fetch_models_button.setEnabled(False)
        self.model_status_label.setText(tr("settings.api.model.fetching"))
        self._model_loader.start(self._model_request_id, provider_id, config, api_key)

    def _on_models_loaded(self, request_id, models, error):
        self._model_request_pending = False
        self.fetch_models_button.setEnabled(True)
        if request_id != self._model_request_id:
            return
        if error:
            self.model_status_label.setText(error)
            return
        self._model_lists[(
            self.selected_api_provider(),
            self.base_url_input.text().strip().rstrip("/"),
        )] = models
        self._populate_models(self.model_combo.currentText())
        self.model_status_label.setText(
            tr("settings.api.model.fetched", count=len(models))
        )

    def _refresh_api_key_status(self) -> None:
        provider = get_api_provider(self.selected_api_provider())
        name = provider_name(provider.provider_id)
        self.api_key_input.setPlaceholderText(
            tr("settings.api.key.placeholder_for", provider=name)
        )

        if provider.provider_id != self._initial_api_provider:
            message = tr(
                "settings.api.status.switching",
                provider=name,
                variable=provider.environment_variable,
            )
        elif self._environment_key_active:
            message = tr(
                "settings.api.status.environment",
                variable=provider.environment_variable,
            )
        elif self._api_key_configured:
            message = tr("settings.api.status.configured")
        else:
            message = tr("settings.api.status.missing")

        if not self.vision_checkbox.isChecked():
            message += tr("settings.api.status.no_vision")
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #909399; font-size: 12px;")

    def launch_at_startup(self) -> bool | None:
        if not self.chk_startup.isEnabled():
            return None
        return self.chk_startup.isChecked()

    def candidate_settings(self) -> AppSettings:
        drafts = {**self._drafts, self.selected_api_provider(): self._field_values()}
        configs = dict(self._initial_settings.api_configs)
        for provider_id, values in drafts.items():
            try:
                config = ApiProviderConfig(*values)
                if not config.model and provider_id == self.selected_api_provider():
                    raise ValueError("missing model")
                if config == provider_default_config(provider_id):
                    configs.pop(provider_id, None)
                else:
                    if not config.model:
                        raise ValueError("missing model")
                    configs[provider_id] = config
            except ValueError as exc:
                raise SettingsDataError(
                    tr("settings.api.invalid_config", provider=provider_name(provider_id))
                ) from exc
        return AppSettings(
            enabled=self.chk_enabled.isChecked(),
            shortcut=self.shortcut_button.binding,
            show_window_shortcut=self.show_window_shortcut_button.binding,
            api_provider=self.selected_api_provider(),
            api_configs=configs,
            back_translation=self.chk_back_translation.isChecked(),
            completion_notifications=self.chk_completion_notifications.isChecked(),
            ui_language=str(self.language_combo.currentData()),
        )

    def accept(self) -> None:
        try:
            self.candidate_settings()
        except SettingsDataError as exc:
            # Only API fields can be invalid; show them where they are fixed.
            self._show_api_error(str(exc))
            self.requested_update = None
            return
        super().accept()

    def api_key(self) -> str:
        return self.api_key_input.text().strip()

    def done(self, result: int) -> None:
        self._model_request_id += 1
        self._model_loader.stop()
        self.shortcut_button.cancel_capture()
        self.show_window_shortcut_button.cancel_capture()
        if self._updates is not None:
            try:
                self._updates.changed.disconnect(self._refresh_update_section)
            except TypeError:
                pass  # Already disconnected by an earlier done().
        super().done(result)


# ================= 3. 单实例本地通信服务 =================
# ================= 4. 主窗口逻辑 =================
class _KeyboardHotkeyEventFilter(QAbstractNativeEventFilter):
    """Receive WM_HOTKEY without overriding the main QWidget nativeEvent."""

    def __init__(self, manager):
        super().__init__()
        self._manager = manager

    def nativeEventFilter(self, _event_type, message):
        try:
            handled = self._manager.handle_native_message(message)
        except Exception:
            handled = False
        return bool(handled), 0


class TranslationShortcutManager(QObject):
    triggered = pyqtSignal()
    _mouse_hook_triggered = pyqtSignal(int)

    _WIN32_MODIFIERS = {
        "ctrl": MOD_CONTROL,
        "alt": MOD_ALT,
        "shift": MOD_SHIFT,
        "win": MOD_WIN,
    }

    def __init__(
        self,
        host_window=None,
        binding: ShortcutBinding | None = DEFAULT_SETTINGS.shortcut,
        *,
        hotkey_id: int = TRANSLATION_HOTKEY_ID,
        log_name: str = "translation",
    ):
        super().__init__(host_window)
        self._host_window = host_window
        self._binding = binding
        self._hotkey_id = int(hotkey_id)
        self._log_name = str(log_name)
        self._enabled = False
        self._suspend_depth = 0
        self._mouse_hook = None
        self._mouse_hook_generation = 0
        self._mouse_hook_triggered.connect(
            self._handle_mouse_hook_triggered,
            Qt.ConnectionType.QueuedConnection,
        )
        self._keyboard_registered = False
        self._native_event_filter = _KeyboardHotkeyEventFilter(self)
        self._native_filter_installed = False
        self._last_trigger_time = 0.0

    @property
    def binding(self) -> ShortcutBinding | None:
        return self._binding

    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(self, binding: ShortcutBinding | None, enabled: bool) -> bool:
        if binding is not None and not isinstance(binding, ShortcutBinding):
            return False
        requested_enabled = bool(enabled and binding is not None)
        if binding == self._binding and requested_enabled == self._enabled:
            return self._reconcile()

        previous_binding = self._binding
        previous_enabled = self._enabled
        if not self._deactivate():
            return False

        self._binding = binding
        self._enabled = requested_enabled
        if self._reconcile():
            return True

        # Registration can fail when another application owns the keyboard
        # combination. Keep the previously active binding in that case.
        self._deactivate()
        self._binding = previous_binding
        self._enabled = previous_enabled
        self._reconcile()
        return False

    def install(self) -> bool:
        return self.configure(self._binding, True)

    def uninstall(self) -> bool:
        if not self._deactivate():
            return False
        self._enabled = False
        self._suspend_depth = 0
        return True

    def suspend(self) -> bool:
        self._suspend_depth += 1
        if self._suspend_depth > 1:
            return True
        if self._deactivate():
            return True
        self._suspend_depth -= 1
        return False

    def resume(self) -> bool:
        if self._suspend_depth:
            self._suspend_depth -= 1
        return self._reconcile()

    def is_installed(self) -> bool:
        mouse_installed = bool(
            self._mouse_hook is not None and self._mouse_hook.is_running()
        )
        return bool(mouse_installed or self._keyboard_registered)

    def handle_native_message(self, message) -> bool:
        if (
            not self._enabled
            or self._suspend_depth
            or not self._keyboard_registered
            or self._binding is None
            or self._binding.kind != "keyboard"
        ):
            return False

        try:
            message_address = int(message)
            if not message_address:
                return False
            native_message = wintypes.MSG.from_address(message_address)
        except (TypeError, ValueError, OSError):
            return False

        if (
            native_message.message == WM_HOTKEY
            and int(native_message.wParam) == self._hotkey_id
        ):
            QTimer.singleShot(0, self._emit_if_active)
            return True
        return False

    def _reconcile(self) -> bool:
        should_be_active = (
            self._binding is not None and self._enabled and self._suspend_depth == 0
        )
        if not should_be_active:
            return self._deactivate()
        if self.is_installed():
            return True
        if self._binding.kind == "mouse":
            return self._start_mouse_hook()
        return self._register_keyboard_hotkey()

    def _start_mouse_hook(self) -> bool:
        if self._mouse_hook is not None and self._mouse_hook.is_running():
            return True
        if self._binding is None or not self._binding.mouse_button:
            return False

        self._mouse_hook_generation += 1
        generation = self._mouse_hook_generation
        hook = WindowsMouseShortcutHook(
            self._binding.mouse_button,
            lambda: self._mouse_hook_triggered.emit(generation),
        )
        if not hook.start():
            hook.stop(timeout=0.25)
            return False
        self._mouse_hook = hook
        return True

    def _handle_mouse_hook_triggered(self, generation: int) -> None:
        if (
            generation != self._mouse_hook_generation
            or self._mouse_hook is None
            or not self._enabled
            or self._suspend_depth
            or self._binding is None
            or self._binding.kind != "mouse"
        ):
            return

        now = time.monotonic()
        if now - self._last_trigger_time < 0.25:
            return
        self._last_trigger_time = now
        self._emit_if_active()

    def _register_keyboard_hotkey(self) -> bool:
        if self._keyboard_registered:
            return True
        if (
            self._host_window is None
            or self._binding is None
            or self._binding.virtual_key is None
        ):
            return False

        modifiers = MOD_NOREPEAT
        for name in self._binding.modifiers:
            modifiers |= self._WIN32_MODIFIERS[name]

        try:
            hwnd = int(self._host_window.winId())
            registered = user32.RegisterHotKey(
                hwnd,
                self._hotkey_id,
                modifiers,
                self._binding.virtual_key,
            )
        except Exception:
            registered = False

        if registered and not self._install_native_event_filter():
            try:
                user32.UnregisterHotKey(hwnd, self._hotkey_id)
            except Exception:
                pass
            registered = False

        self._keyboard_registered = bool(registered)
        if not registered:
            log(
                f"[Shortcut] {self._log_name} keyboard hotkey "
                "registration failed"
            )
        return bool(registered)

    def _install_native_event_filter(self) -> bool:
        if self._native_filter_installed:
            return True
        app = QApplication.instance()
        if app is None:
            return False
        try:
            app.installNativeEventFilter(self._native_event_filter)
        except (RuntimeError, TypeError):
            return False
        self._native_filter_installed = True
        return True

    def _remove_native_event_filter(self) -> None:
        if not self._native_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeNativeEventFilter(self._native_event_filter)
            except (RuntimeError, TypeError):
                pass
        self._native_filter_installed = False

    def _deactivate(self) -> bool:
        mouse_ok = self._stop_mouse_hook()
        keyboard_ok = self._unregister_keyboard_hotkey()
        return mouse_ok and keyboard_ok

    def _stop_mouse_hook(self) -> bool:
        hook = self._mouse_hook
        self._mouse_hook_generation += 1
        if hook is None:
            return True
        stopped = hook.stop()
        if stopped:
            self._mouse_hook = None
        return stopped

    def _unregister_keyboard_hotkey(self) -> bool:
        if not self._keyboard_registered:
            self._remove_native_event_filter()
            return True
        if self._host_window is None:
            return False

        try:
            removed = bool(
                user32.UnregisterHotKey(
                    int(self._host_window.winId()),
                    self._hotkey_id,
                )
            )
        except Exception:
            removed = False
        if not removed:
            return False

        self._keyboard_registered = False
        self._remove_native_event_filter()
        log(f"[Shortcut] {self._log_name} keyboard hotkey unregistered")
        return True

    def _emit_if_active(self) -> None:
        if self._enabled and not self._suspend_depth and self.is_installed():
            self.triggered.emit()


# Backward-compatible name for callers that imported the original class.
XButton1MouseHook = TranslationShortcutManager


class _IpcRequestCompletion:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.result = ReceiveResult.reject(RejectionReason.NOT_READY)
        self._lock = threading.Lock()
        self._state = "pending"

    def claim(self) -> bool:
        with self._lock:
            if self._state != "pending":
                return False
            self._state = "claimed"
            return True

    def cancel_if_pending(self) -> bool:
        with self._lock:
            if self._state != "pending":
                return False
            self._state = "cancelled"
            self.done.set()
            return True

    def complete(self, result: ReceiveResult) -> None:
        with self._lock:
            if self._state != "claimed":
                return
            self.result = result
            self._state = "done"
            self.done.set()


class QtIpcRequestBridge(QObject):
    """Synchronously bridge the pipe thread to the Qt GUI thread."""

    request_ready = pyqtSignal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self._window = None
        self._ready = threading.Event()
        self._failure_reason: RejectionReason | None = None
        self.request_ready.connect(
            self._dispatch,
            Qt.ConnectionType.QueuedConnection,
        )

    def bind(self, window) -> None:
        self._window = window
        self._failure_reason = None
        self._ready.set()

    def fail(self, reason: RejectionReason = RejectionReason.STARTUP_FAILED) -> None:
        self._failure_reason = reason
        self._ready.set()

    def receive(self, request: AppRequest) -> ReceiveResult:
        if not self._ready.wait(IPC_UI_TIMEOUT_SECONDS):
            return ReceiveResult.reject(RejectionReason.NOT_READY)
        if self._failure_reason is not None or self._window is None:
            return ReceiveResult.reject(
                self._failure_reason or RejectionReason.NOT_READY
            )

        completion = _IpcRequestCompletion()
        self.request_ready.emit(request, completion)
        if not completion.done.wait(IPC_UI_TIMEOUT_SECONDS):
            if completion.cancel_if_pending():
                return ReceiveResult.reject(RejectionReason.NOT_READY)
            # The GUI already claimed the request.  Wait for its explicit
            # ownership decision instead of returning a rejection that could
            # later race with a started operation.
            completion.done.wait()
        return completion.result

    def _dispatch(self, request: AppRequest, completion: _IpcRequestCompletion) -> None:
        if not completion.claim():
            return
        try:
            if self._failure_reason is not None or self._window is None:
                result = ReceiveResult.reject(
                    self._failure_reason or RejectionReason.NOT_READY
                )
            else:
                result = self._window.handle_app_request(
                    request,
                    allow_key_prompt=False,
                )
            result = (
                result
                if isinstance(result, ReceiveResult)
                else ReceiveResult.reject(RejectionReason.HANDLER_FAILED)
            )
            record_event(
                AppEvent.IPC_REQUEST_ACCEPTED
                if result.accepted
                else AppEvent.IPC_REQUEST_REJECTED
            )
            if not result.accepted and result.reason in {
                RejectionReason.MISSING_KEY,
                RejectionReason.BUSY,
            }:
                self._window.force_show_window()
                message = tr(
                    "message.ipc_no_key"
                    if result.reason is RejectionReason.MISSING_KEY
                    else "message.ipc_ocr_busy"
                )
                self._window.set_result_message(message)
        except Exception:
            result = ReceiveResult.reject(
                RejectionReason.HANDLER_FAILED
            )
            record_event(AppEvent.IPC_REQUEST_REJECTED)
        finally:
            completion.complete(result)


def pane_message_format(error: bool = False) -> QTextCharFormat:
    """Status text in a text pane, set apart from translated text by colour."""
    message_format = QTextCharFormat()
    message_format.setForeground(QColor("#C45656" if error else "#909399"))
    return message_format


RESULT_ERROR_NOTICE_STYLE = """
    #resultNotice {
        background-color: #FEF0F0;
        border: 1px solid #FBD5D5;
        border-radius: 8px;
    }
    #resultNotice QLabel {
        background-color: transparent;
        color: #C45656;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 12px;
    }
    #resultNoticeIcon {
        background-color: #F56C6C;
        color: #FFFFFF;
        border-radius: 8px;
        font-size: 11px;
        font-weight: 700;
    }
    #resultNotice QPushButton {
        background-color: #FFFFFF;
        color: #C45656;
        border: 1px solid #FBC4C4;
        border-radius: 6px;
        padding: 3px 10px;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 12px;
        font-weight: 600;
    }
    #resultNotice QPushButton:hover {
        background-color: #FDE2E2;
        border-color: #F56C6C;
    }
"""

RESULT_INFO_NOTICE_STYLE = """
    #resultNotice {
        background-color: #F4F4F5;
        border: 1px solid #E9E9EB;
        border-radius: 8px;
    }
    #resultNotice QLabel {
        background-color: transparent;
        color: #909399;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 12px;
    }
    #resultNoticeIcon {
        background-color: #C0C4CC;
        color: #FFFFFF;
        border-radius: 8px;
        font-size: 11px;
        font-weight: 700;
    }
"""


class TranslationWindow(QWidget):
    def __init__(
        self,
        paths: AppPaths | None = None,
        credential_store: WindowsCredentialStore | None = None,
        initial_settings: AppSettings | None = None,
    ):
        super().__init__()

        self.translation_unread = False
        self._base_app_icon = QIcon()
        self._base_tray_tooltip = APP_DISPLAY_NAME

        self.app_paths = paths or APP_PATHS
        if self.app_paths is None:
            raise RuntimeError("application paths are not initialized")
        self.credential_store = credential_store or CREDENTIAL_STORE
        self.settings_path = self.app_paths.data_dir / SETTINGS_FILE_NAME
        self.app_settings = (
            initial_settings
            if initial_settings is not None
            else self.load_app_settings()
        )
        set_ui_language(self.app_settings.ui_language)

        self.source_mode = "manual"         # manual / snipdo
        self.content_mode_override = "auto"  # auto / dictionary
        self.dictionary_source_lang = "auto"
        self.dictionary_target_lang = "default"
        self.pending_snipdo_text = ""
        self.original_paragraphs = []
        self.full_translation = ""
        self.force_quit = False
        self._show_geometry = None
        self.trans_thread = None
        self.translation_threads = set()
        self.align_thread = None
        self.ocr_thread = None
        self.ocr_result_source_mode = "manual"
        self.alignment_selection_source = None
        self.alignment_selection_range = None
        self.alignment_highlight_source = None
        self.alignment_target_widget = None
        self.alignment_selected_text = ""
        self.alignment_selected_sentence = ""
        self.alignment_selection_is_sentence = False
        self.selection_capture_busy = False
        self._selection_capture_hwnd = 0
        self._selection_capture_binding = None
        self._selection_release_deadline = 0.0
        self._selection_suspended_managers = []
        self.shortcut_manager = None
        self.show_window_shortcut_manager = None
        self.xbutton1_hook = None
        self.current_request_is_structured = False
        self.active_translation_text = ""
        self.active_translation_mode = ""
        self.translation_history = self.load_translation_history()
        self.connection_refresh_timer = QTimer(self)
        self.connection_refresh_timer.setInterval(CONNECTION_REFRESH_CHECK_MS)
        self.connection_refresh_timer.timeout.connect(self.refresh_api_connection)
        self.live_render_timer = QTimer(self)
        self.live_render_timer.setSingleShot(True)
        self.live_render_timer.timeout.connect(self.on_live_render_timer)
        self._live_render_due = 0.0
        self.back_thread = None
        self.back_translation_text = ""
        self.back_translation_language_code = None
        self.heading_mode = "auto"
        self.back_render_timer = QTimer(self)
        self.back_render_timer.setSingleShot(True)
        self.back_render_timer.setInterval(int(LIVE_RENDER_INTERVAL_SECONDS * 1000))
        self.back_render_timer.timeout.connect(self.render_back_translation)

        self.init_ui()
        self.setup_result_format()
        self.update_notifications = UpdateNotifications(self)
        self.update_notifications.changed.connect(self.refresh_update_indicator)
        self.setup_tray_icon()
        self.setup_translation_shortcut()
        self.apply_manual_mode_ui()
        # Do not create a native HWND solely for a debug message.  Native
        # handle creation synchronously dispatches Windows messages, so keep
        # construction free of that unnecessary platform side effect.  A
        # handle is requested later only when foreground activation needs it.
        log("[UI] TranslationWindow initialized")

    def load_app_settings(self) -> AppSettings:
        try:
            return load_settings(self.settings_path)
        except (SettingsDataError, OSError):
            return DEFAULT_SETTINGS

    # ---------- 托盘 ----------
    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)

        fallback_icon = self.style().standardIcon(
            self.style().StandardPixmap.SP_ComputerIcon
        )
        self.enabled_app_icon = (
            QIcon(str(self.app_paths.icon_path))
            if self.app_paths.icon_path.is_file()
            else fallback_icon
        )
        self.disabled_app_icon = (
            QIcon(str(self.app_paths.disabled_icon_path))
            if self.app_paths.disabled_icon_path.is_file()
            else fallback_icon
        )
        icon = (
            self.enabled_app_icon
            if self.app_settings.enabled
            else self.disabled_app_icon
        )

        self._base_app_icon = icon
        self._base_tray_tooltip = APP_DISPLAY_NAME
        self.refresh_notification_visuals()

        # QSystemTrayIcon does not own its context menu.  Keep both a Python
        # reference and a QObject parent so the native tray integration never
        # observes a menu that has already been destroyed.
        self.tray_menu = QMenu(self)
        tray_menu = self.tray_menu
        tray_menu.setStyleSheet(MENU_STYLE)

        self.show_window_action = QAction(tr("tray.show_window"), self)
        self.show_window_action.triggered.connect(self.show_manual_window)
        tray_menu.addAction(self.show_window_action)

        self.toggle_translation_action = QAction(tr("tray.disable"), self)
        self.toggle_translation_action.setCheckable(True)
        self.toggle_translation_action.setChecked(not self.app_settings.enabled)
        self.toggle_translation_action.triggered.connect(
            self.toggle_global_translation
        )
        tray_menu.addAction(self.toggle_translation_action)

        self.settings_action = QAction(tr("tray.settings"), self)
        self.settings_action.triggered.connect(
            lambda _checked=False: self.show_settings()
        )
        tray_menu.addAction(self.settings_action)

        self.ocr_action = QAction(tr("tray.ocr_clipboard"), self)
        self.ocr_action.triggered.connect(self.start_clipboard_ocr)
        tray_menu.addAction(self.ocr_action)

        tray_menu.addSeparator()

        self.check_updates_action = QAction(tr("tray.check_updates"), self)
        self.check_updates_action.triggered.connect(
            lambda _checked=False: self.check_for_updates()
        )
        tray_menu.addAction(self.check_updates_action)

        self.about_action = QAction(tr("tray.about", version=APP_VERSION), self)
        self.about_action.triggered.connect(
            lambda _checked=False: self.show_settings(page="about")
        )
        tray_menu.addAction(self.about_action)
        tray_menu.addSeparator()

        self.quit_action = QAction(tr("tray.quit"), self)
        self.quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(self.quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.messageClicked.connect(self.on_tray_message_clicked)
        self.tray_icon.show()

    def check_for_updates(self):
        """Check now and show the outcome on the About page, never in a pop-up."""
        updates = self.update_notifications
        updates.check_manually()
        # While an update downloads, the check shows its progress instead.
        if not updates.is_installing:
            self.show_settings(page="about")

    def refresh_update_indicator(self):
        """Swap the Settings gear for a blue download icon while an update waits."""
        release = self.update_notifications.available_release
        self.btn_settings.setIcon(
            self._update_icon if release is not None else self._settings_icon
        )
        self.btn_settings.setAccessibleName(
            tr("main.settings_update", version=release.version)
            if release is not None else tr("main.settings")
        )
        self.refresh_shortcut_status()

    def on_settings_button_clicked(self):
        # With an update waiting, the download icon leads straight to it.
        self.show_settings(
            page="about" if self.update_notifications.available_release else ""
        )

    def refresh_notification_visuals(self):
        if self._base_app_icon.isNull() or not hasattr(self, "tray_icon"):
            return

        icon = (
            create_unread_badge_icon(self._base_app_icon)
            if self.translation_unread
            else self._base_app_icon
        )
        self.tray_icon.setIcon(icon)
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)

        tooltip = self._base_tray_tooltip
        if self.translation_unread:
            tooltip += tr("tray.unread")
        self.tray_icon.setToolTip(tooltip)

    def set_translation_unread(self, unread: bool):
        unread = bool(unread)
        if self.translation_unread == unread:
            return
        self.translation_unread = unread
        self.refresh_notification_visuals()

    def clear_translation_unread(self):
        self.set_translation_unread(False)

    def is_translation_window_focused(self) -> bool:
        return bool(
            self.isVisible()
            and not self.isMinimized()
            and self.isActiveWindow()
        )

    def notify_translation_completed(self) -> bool:
        """Notify only when the completed translation is not being viewed."""
        if self.is_translation_window_focused():
            self.clear_translation_unread()
            return False

        self.set_translation_unread(True)
        if not self.app_settings.completion_notifications:
            return True
        try:
            self.tray_icon.showMessage(
                tr("notify.translation_finished"),
                self.full_translation.strip(),
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            log("[Notification] translation completion shown")
        except Exception as e:
            log(f"[Notification] show completion error: {e}")
        return True

    def setup_translation_shortcut(self):
        self.shortcut_manager = TranslationShortcutManager(
            self,
            self.app_settings.shortcut,
        )
        # Retain the old attribute for compatibility with existing lifecycle
        # code and third-party imports.
        self.xbutton1_hook = self.shortcut_manager
        self.shortcut_manager.triggered.connect(
            self.on_translation_shortcut_triggered
        )

        if not self.shortcut_manager.configure(
            self.app_settings.shortcut,
            self.app_settings.enabled,
        ):
            self.tray_icon.showMessage(
                APP_DISPLAY_NAME,
                tr("notify.shortcut_failed"),
                QSystemTrayIcon.MessageIcon.Warning,
                1800,
            )

        self.show_window_shortcut_manager = TranslationShortcutManager(
            self,
            self.app_settings.show_window_shortcut,
            hotkey_id=SHOW_WINDOW_HOTKEY_ID,
            log_name="show-window",
        )
        self.show_window_shortcut_manager.triggered.connect(
            self.on_show_window_shortcut_triggered
        )
        if not self.show_window_shortcut_manager.configure(
            self.app_settings.show_window_shortcut,
            self.app_settings.show_window_shortcut is not None,
        ):
            self.tray_icon.showMessage(
                APP_DISPLAY_NAME,
                tr("notify.show_window_shortcut_failed"),
                QSystemTrayIcon.MessageIcon.Warning,
                1800,
            )
        self.refresh_shortcut_status()

    def setup_xbutton1_hook(self):
        """Compatibility wrapper for the original hard-coded setup method."""
        self.setup_translation_shortcut()

    def shutdown_translation_shortcut(self):
        for attribute_name in (
            "shortcut_manager",
            "show_window_shortcut_manager",
        ):
            manager = getattr(self, attribute_name, None)
            if manager:
                manager.uninstall()

    def refresh_shortcut_status(self):
        shortcut_active = bool(
            self.app_settings.enabled
            and self.shortcut_manager
            and self.shortcut_manager.is_installed()
        )
        if not self.app_settings.enabled:
            tooltip = tr("tray.disabled", app=APP_DISPLAY_NAME)
        elif shortcut_active:
            tooltip = f"{APP_DISPLAY_NAME} · {shortcut_label(self.app_settings.shortcut)}"
        else:
            tooltip = tr("tray.shortcut_inactive", app=APP_DISPLAY_NAME)
        show_binding = self.app_settings.show_window_shortcut
        show_shortcut_active = bool(
            show_binding is not None
            and getattr(self, "show_window_shortcut_manager", None)
            and self.show_window_shortcut_manager.is_installed()
        )
        if show_binding is None:
            show_shortcut_status = tr("shortcut.not_set")
        else:
            show_shortcut_status = (
                shortcut_label(show_binding) if show_shortcut_active else tr("shortcut.inactive")
            )
        tooltip += tr("tray.show_window_status", status=show_shortcut_status)
        icon = (
            self.enabled_app_icon
            if shortcut_active
            else self.disabled_app_icon
        )
        self._base_app_icon = icon
        self._base_tray_tooltip = tooltip
        self.refresh_notification_visuals()
        if hasattr(self, "toggle_translation_action"):
            self.toggle_translation_action.setChecked(
                not self.app_settings.enabled
            )
        if hasattr(self, "btn_settings"):
            button_tooltip = (
                tr("main.settings_tooltip", shortcut=shortcut_label(self.app_settings.shortcut))
                + f"\n{tooltip}"
            )
            updates = getattr(self, "update_notifications", None)
            release = updates.available_release if updates is not None else None
            if release is not None:
                button_tooltip = (
                    tr("main.update_tooltip", version=release.version)
                    + f"\n{button_tooltip}"
                )
            self.btn_settings.setToolTip(button_tooltip)
        if hasattr(self, "show_window_action"):
            self.show_window_action.setText(
                tr("tray.show_window_with_shortcut", shortcut=shortcut_label(show_binding))
                if show_binding is not None else tr("tray.show_window")
            )
        if hasattr(self, "result_busy"):
            self.refresh_result_hint(shortcut_active)

    def refresh_result_hint(self, shortcut_active: bool | None = None):
        """While nothing is translated, say where the translation will appear."""
        if not hasattr(self, "result_busy"):
            return
        if shortcut_active is None:
            shortcut_active = bool(
                self.app_settings.enabled
                and self.shortcut_manager
                and self.shortcut_manager.is_installed()
            )
        if self.source_mode != "manual":
            hint = ""
        elif shortcut_active:
            hint = tr(
                "main.placeholder.result_shortcut",
                shortcut=shortcut_label(self.app_settings.shortcut),
            )
        else:
            hint = tr("main.placeholder.result")
        self.result_busy.set_idle_text(hint)

    def refresh_api_status(self):
        if not hasattr(self, "lbl_model_name"):
            return
        provider = self.app_settings.resolved_provider()
        self.lbl_model_name.setText(
            f"{provider_name(provider.provider_id, short=True)} · {provider.model}"
        )
        self.lbl_model_name.setToolTip(
            tr(
                "main.model_tooltip",
                provider=provider_name(provider.provider_id),
                url=provider.base_url,
                model=provider.model,
            )
        )

    def suspend_xbutton1_hook(self):
        if self.shortcut_manager:
            self.shortcut_manager.suspend()

    def restore_xbutton1_hook(self):
        if self.force_quit:
            return
        if self.shortcut_manager:
            self.shortcut_manager.resume()
            self.refresh_shortcut_status()

    def on_xbutton1_triggered(self):
        """Compatibility wrapper for the original XButton1 callback."""
        self.on_translation_shortcut_triggered()

    def on_translation_shortcut_triggered(self):
        if not self.app_settings.enabled or self.force_quit:
            return
        if self.selection_capture_busy:
            log("[Shortcut] capture skipped: busy")
            return

        # Remember the source before stopping hooks or processing more events.
        source_hwnd = int(user32.GetForegroundWindow() or 0)
        if not source_hwnd:
            return
        self._selection_capture_hwnd = source_hwnd
        self._selection_capture_binding = self.app_settings.shortcut
        self._selection_release_deadline = (
            time.monotonic() + SELECTION_KEY_RELEASE_TIMEOUT_SECONDS
        )
        self.selection_capture_busy = True
        self._selection_suspended_managers = []
        # Either shortcut can be configured as Ctrl+C. Both must be inactive
        # while copying, and only successful suspensions should be resumed.
        for manager in (self.shortcut_manager, self.show_window_shortcut_manager):
            if manager is None:
                continue
            try:
                suspended = manager.suspend()
            except Exception:
                suspended = False
            if not suspended:
                self._finish_selection_capture()
                return
            self._selection_suspended_managers.append(manager)
        QTimer.singleShot(35, self._wait_for_selection_shortcut_release)

    def refresh_api_connection(self):
        warmer = connection_warmer(api_runtime.client)
        if warmer is None or not warmer.keep_warm():
            self.connection_refresh_timer.stop()

    def _selection_capture_is_current(self) -> bool:
        return bool(
            self.selection_capture_busy
            and self.app_settings.enabled
            and not self.force_quit
            and self._selection_capture_binding == self.app_settings.shortcut
            and self._selection_capture_hwnd
            and int(user32.GetForegroundWindow() or 0) == self._selection_capture_hwnd
        )

    def _wait_for_selection_shortcut_release(self):
        if not self.selection_capture_busy:
            return
        try:
            if not self._selection_capture_is_current():
                self._finish_selection_capture()
                return
            if time.monotonic() >= self._selection_release_deadline:
                self.tray_icon.showMessage(
                    APP_DISPLAY_NAME,
                    tr("notify.release_shortcut"),
                    QSystemTrayIcon.MessageIcon.Information,
                    1800,
                )
                self._finish_selection_capture()
                return
            if not copy_keys_released(self._selection_capture_binding.virtual_key):
                QTimer.singleShot(15, self._wait_for_selection_shortcut_release)
                return
            self.translate_current_selection()
        except Exception as e:
            log(f"[Shortcut] release wait failed: {e}")
            self._finish_selection_capture()

    def _finish_selection_capture(self):
        managers = self._selection_suspended_managers
        self._selection_suspended_managers = []
        self.selection_capture_busy = False
        self._selection_capture_hwnd = 0
        self._selection_capture_binding = None
        self._selection_release_deadline = 0.0
        QTimer.singleShot(150, lambda: self._resume_selection_shortcuts(managers))

    def _resume_selection_shortcuts(self, managers):
        if self.force_quit:
            return
        for manager in managers:
            manager.resume()
        self.refresh_shortcut_status()

    def capture_current_selection_text(self) -> str:
        if not self._selection_capture_is_current():
            return ""
        trigger_key = self._selection_capture_binding.virtual_key
        if not copy_keys_released(trigger_key):
            return ""
        clipboard = QApplication.clipboard()
        original_mime = clone_clipboard_mime_data()
        sentinel = f"__gptsapi_selection_probe_{uuid.uuid4()}__"

        try:
            clipboard.setText(sentinel)
            QApplication.processEvents()
            # A failed clipboard write must never turn old clipboard text into
            # a selection. Recheck keys/focus after the nested event processing.
            if clipboard.text() != sentinel:
                return ""
            sentinel_sequence = get_clipboard_sequence_number()
            if (
                not sentinel_sequence
                or not self._selection_capture_is_current()
                or not copy_keys_released(trigger_key)
                or not send_ctrl_c()
            ):
                return ""

            deadline = time.monotonic() + SELECTION_COPY_TIMEOUT_SECONDS
            captured_text = ""

            while time.monotonic() < deadline:
                QApplication.processEvents()
                if not self._selection_capture_is_current():
                    return ""
                sequence = get_clipboard_sequence_number()
                if sequence and sequence != sentinel_sequence:
                    current_text = clipboard_mime_to_formatted_text(
                        clipboard.mimeData(),
                        sentinel,
                    )
                    if current_text:
                        captured_text = current_text
                        break

                time.sleep(0.015)

            return captured_text.strip()
        finally:
            try:
                if original_mime is not None:
                    clipboard.setMimeData(original_mime)
                else:
                    clipboard.clear()
                QApplication.processEvents()
            except Exception as e:
                log(f"[XButton1] restore clipboard error: {e}")

    def translate_current_selection(self):
        try:
            if not self._selection_capture_is_current():
                return
            log("[Shortcut] triggered")
            selected_text = self.capture_current_selection_text()
            if not self._selection_capture_is_current():
                return

            if not selected_text:
                log("[Shortcut] no selected text captured")
                self.tray_icon.showMessage(
                    APP_DISPLAY_NAME,
                    tr("notify.no_selection"),
                    QSystemTrayIcon.MessageIcon.Information,
                    1800,
                )
                return

            self.handle_new_request(selected_text)
        except Exception as e:
            log(f"[Shortcut] translate_current_selection error: {e}")
        finally:
            self._finish_selection_capture()

    def on_tray_activated(self, reason):
        log(f"[Tray] activated: {reason}")
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick
        ):
            if self.translation_unread:
                self.force_show_window()
                self.clear_translation_unread()
            else:
                self.show_manual_window()

    def on_tray_message_clicked(self):
        log("[Notification] translation completion clicked")
        self.force_show_window()
        self.clear_translation_unread()

    def on_show_window_shortcut_triggered(self):
        log("[Shortcut] show window triggered")
        self.force_show_window()

    def show_settings(self, page: str = ""):
        self.force_show_window()
        provider = self.app_settings.resolved_provider()
        startup_enabled = False
        startup_available = True
        try:
            startup_registration = WindowsStartupRegistration(__file__)
            startup_enabled = startup_registration.is_enabled()
        except StartupRegistrationError:
            startup_available = False
        dialog = SettingsDialog(
            self.app_settings,
            api_key_configured=(
                client is not None
                and api_runtime.provider.provider_id == provider.provider_id
            ),
            environment_key_active=not is_placeholder_api_key(
                os.getenv(provider.environment_variable, "")
            ),
            startup_enabled=startup_enabled,
            startup_available=startup_available,
            updates=self.update_notifications,
            page=page,
            changelog=load_changelog(self.app_paths.bundle_dir, ui_language()),
            app_icon=getattr(self, "enabled_app_icon", None),
            parent=self,
        )
        shortcut_managers = [
            manager
            for manager in (
                getattr(self, "shortcut_manager", None),
                getattr(self, "show_window_shortcut_manager", None),
            )
            if manager is not None
        ]
        for manager in shortcut_managers:
            manager.suspend()
        try:
            result = dialog.exec()
        finally:
            for manager in shortcut_managers:
                manager.resume()
            self.refresh_shortcut_status()

        if result != QDialog.DialogCode.Accepted:
            return
        if not self.apply_settings(dialog.candidate_settings(), dialog.api_key()):
            return
        requested_startup = dialog.launch_at_startup()
        if requested_startup is not None and startup_available:
            try:
                startup_registration.set_enabled(requested_startup)
            except StartupRegistrationError:
                show_warning(
                    self,
                    tr("warning.startup.title"),
                    tr("warning.startup.text"),
                )
        # Settings is closed by now, so the progress window stays usable.
        if dialog.requested_update is not None:
            self.update_notifications.install(dialog.requested_update)

    def toggle_global_translation(self):
        candidate = replace(
            self.app_settings,
            enabled=not self.app_settings.enabled,
        )
        self.apply_settings(candidate)

    def apply_settings(self, candidate: AppSettings, api_key: str = "") -> bool:
        previous = self.app_settings
        if shortcuts_conflict(
            candidate.shortcut,
            candidate.show_window_shortcut,
        ):
            show_warning(
                self,
                tr("warning.shortcut_conflict.title"),
                tr("warning.shortcut_conflict.text"),
            )
            return False

        provider_changed = candidate.resolved_provider() != previous.resolved_provider()
        candidate_config = candidate.api_configs.get(candidate.api_provider)
        client_options = {"config": candidate_config} if candidate_config is not None else {}
        target_store = credential_store_for_window(
            self,
            candidate.api_provider,
        )
        candidate_client = None
        normalized_key = (api_key or "").strip()
        if normalized_key:
            candidate_client = create_api_client(
                normalized_key,
                candidate.api_provider,
                **client_options,
            )
            if candidate_client is None:
                show_warning(
                    self,
                    tr("warning.invalid_key.title"),
                    tr("warning.invalid_key.text"),
                )
                return False
        elif provider_changed:
            if (
                candidate.api_provider == previous.api_provider
                and candidate.resolved_provider().base_url == previous.resolved_provider().base_url
                and api_runtime.provider == previous.resolved_provider()
            ):
                candidate_client = client
            else:
                selected_key = provider_api_key(candidate.api_provider, target_store)
                if selected_key:
                    candidate_client = create_api_client(
                        selected_key, candidate.api_provider, **client_options,
                    )

        if provider_changed:
            for method_name in (
                "cancel_current_ocr",
                "cancel_current_alignment",
                "cancel_current_translation",
            ):
                method = getattr(self, method_name, None)
                if callable(method):
                    method()
            running_threads = []
            for attribute_name in (
                "ocr_thread",
                "align_thread",
                "trans_thread",
            ):
                thread = getattr(self, attribute_name, None)
                try:
                    is_running = bool(thread and thread.isRunning())
                except Exception:
                    is_running = True
                if is_running:
                    running_threads.append(attribute_name)
            if running_threads:
                show_warning(
                    self,
                    tr("warning.provider_busy.title"),
                    tr("warning.provider_busy.text"),
                )
                return False

        translation_manager = getattr(self, "shortcut_manager", None)
        show_window_manager = getattr(
            self,
            "show_window_shortcut_manager",
            None,
        )
        if translation_manager and not translation_manager.configure(
            candidate.shortcut,
            candidate.enabled,
        ):
            show_warning(
                self,
                tr("warning.shortcut_unavailable.title"),
                tr("warning.shortcut_unavailable.text"),
            )
            self.refresh_shortcut_status()
            return False

        if show_window_manager and not show_window_manager.configure(
            candidate.show_window_shortcut,
            candidate.show_window_shortcut is not None,
        ):
            if translation_manager:
                translation_manager.configure(
                    previous.shortcut,
                    previous.enabled,
                )
            show_warning(
                self,
                tr("warning.show_window_shortcut_unavailable.title"),
                tr("warning.shortcut_unavailable.text"),
            )
            self.refresh_shortcut_status()
            return False

        try:
            save_settings_atomic(self.settings_path, candidate)
        except (OSError, SettingsDataError):
            if translation_manager:
                translation_manager.configure(
                    previous.shortcut,
                    previous.enabled,
                )
            if show_window_manager:
                show_window_manager.configure(
                    previous.show_window_shortcut,
                    previous.show_window_shortcut is not None,
                )
            show_warning(
                self,
                tr("warning.save_failed.title"),
                tr("warning.save_failed.text"),
            )
            self.refresh_shortcut_status()
            return False

        self.app_settings = candidate
        # Switch before the tray and labels below are rebuilt.
        previous_language = ui_language()
        set_ui_language(candidate.ui_language)
        if candidate.ui_language != previous_language:
            retranslate_ui = getattr(self, "retranslate_ui", None)
            if callable(retranslate_ui):
                retranslate_ui(previous_language)
        self.refresh_shortcut_status()
        refresh_api_status = getattr(self, "refresh_api_status", None)
        if callable(refresh_api_status):
            refresh_api_status()
        sync_back_translation = getattr(self, "sync_back_translation", None)
        if candidate.back_translation != previous.back_translation and callable(
            sync_back_translation
        ):
            sync_back_translation()

        if normalized_key:
            try:
                stored = bool(
                    target_store
                    and target_store.write(normalized_key)
                )
            except Exception:
                stored = False

            if stored:
                self.credential_store = target_store
                activate_api_runtime(candidate.api_provider, candidate_client, candidate_config)
                record_event(AppEvent.CREDENTIAL_AVAILABLE)
            else:
                if provider_changed:
                    self.credential_store = target_store
                    activate_api_runtime(candidate.api_provider, None, candidate_config)
                record_event(AppEvent.CREDENTIAL_UNAVAILABLE)
                show_warning(
                    self,
                    tr("warning.key_not_stored.title"),
                    tr("warning.key_not_stored.text"),
                )
                return False
        elif provider_changed:
            self.credential_store = target_store
            activate_api_runtime(candidate.api_provider, candidate_client, candidate_config)
            record_event(
                AppEvent.CREDENTIAL_AVAILABLE
                if candidate_client is not None
                else AppEvent.CREDENTIAL_MISSING
            )

        return True

    def show_manual_window(self):
        log("[UI] show_manual_window called")
        self.cancel_current_ocr()
        self.cancel_current_translation()
        self.source_mode = "manual"
        self.apply_manual_mode_ui(reset_content=True)

        self.force_show_window()
        self.txt_origin.setFocus()

    def quit_app(self):
        log("[App] quit_app called")
        self.force_quit = True
        self.cancel_current_ocr()
        self.cancel_current_translation()
        self.cancel_current_back_translation(wait_ms=800)
        self.shutdown_translation_shortcut()
        self.tray_icon.hide()
        QApplication.quit()

    def cancel_current_translation(self):
        worker = self.trans_thread
        self.trans_thread = None
        if worker and worker.isRunning():
            log("[UI] cancel_current_translation")
            worker.request_stop()
            worker.wait(800)
        self.refresh_stop_button()

    def cancel_current_alignment(self):
        if self.align_thread and self.align_thread.isRunning():
            log("[UI] cancel_current_alignment")
            self.align_thread.request_stop()
            self.align_thread.wait(300)

    def cancel_current_ocr(self):
        if self.ocr_thread and self.ocr_thread.isRunning():
            log("[UI] cancel_current_ocr")
            self.ocr_thread.request_stop()
            self.ocr_thread.wait(800)

    def force_show_window(self):
        """
        在 Windows 下可靠显示主窗口。
        先通过 Qt 恢复并显示窗口，再调用 Win32 API 尝试把窗口带到前台。
        """
        log("[UI] force_show_window called")

        try:
            was_visible = self.isVisible()
            # Moving a hidden maximized window un-maximizes it, so it keeps its place.
            if not was_visible and not self.isMaximized():
                screen = QApplication.primaryScreen()
                if screen:
                    available = screen.availableGeometry()
                    x = available.x() + max(
                        40,
                        (available.width() - self.width()) // 2,
                    )
                    y = available.y() + max(
                        40,
                        (available.height() - self.height()) // 2,
                    )
                    self.move(x, y)
                # Qt rescales a hidden window wrongly once it reappears on a
                # monitor with another display scale (e.g. 150% -> 100%);
                # resizeEvent puts this geometry back.
                self._show_geometry = (self.pos(), self.size())

            if self.isMinimized():
                self.showNormal()
            self.show()
            self.setHidden(False)
            self.raise_()
            self.activateWindow()

            hwnd = int(self.winId())
            win32_force_foreground(hwnd)

            log(f"[UI] window shown, hwnd={hwnd}, pos=({self.x()}, {self.y()}), size=({self.width()}x{self.height()})")

            QTimer.singleShot(120, self._force_activate_only)

        except Exception as e:
            log(f"[UI] force_show_window error: {e}")

    def _force_activate_only(self):
        self._show_geometry = None
        try:
            if self.isMinimized():
                self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()

            hwnd = int(self.winId())
            win32_force_foreground(hwnd)

            log(f"[UI] window re-activated, hwnd={hwnd}, visible={self.isVisible()}, minimized={self.isMinimized()}")
        except Exception as e:
            log(f"[UI] force_show_window activate-only stage error: {e}")

    def paintEvent(self, event):
        card = self.card_frame.geometry()
        key = (card.size(), self.devicePixelRatioF())
        if key != self._card_shadow_key:
            self._card_shadow = render_card_shadow(*key)
            self._card_shadow_key = key
        shadow, offset = self._card_shadow
        painter = QPainter(self)
        # The card covers its own middle, so skip blending the image there.
        painter.setClipRegion(
            QRegion(self.rect()).subtracted(QRegion(card.adjusted(12, 12, -12, -12)))
        )
        painter.drawPixmap(card.topLeft() + offset, shadow)
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._show_geometry is None or not event.spontaneous():
            return
        pos, size = self._show_geometry
        if event.size() == size or self.isMaximized():
            return
        self._show_geometry = None
        self.resize(size)
        self.move(pos)

    # ---------- UI ----------
    def init_ui(self):
        self.setWindowTitle(f"{APP_DISPLAY_NAME} {APP_VERSION}")
        self.setMinimumSize(NORMAL_WINDOW_WIDTH, NORMAL_WINDOW_HEIGHT)
        self.resize(NORMAL_WINDOW_WIDTH, NORMAL_WINDOW_HEIGHT)
        # Rules name the widget they style; a bare rule reaches every widget
        # inside, and labels and text boxes, being frames, drew the card's
        # outline around themselves.
        self.setObjectName("translationWindow")
        self.setStyleSheet(
            "#translationWindow { background-color: #F5F7FA; }" + TOOLTIP_STYLE
        )

        screen = QApplication.primaryScreen()
        if screen:
            center_point = screen.availableGeometry().center()
            frame_geometry = self.frameGeometry()
            frame_geometry.moveCenter(center_point)
            self.move(frame_geometry.topLeft())

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(0)

        self.card_frame = QFrame()
        self.card_frame.setObjectName("card")
        self.card_frame.setStyleSheet("""
            #card {
                background-color: #FFFFFF;
                border-radius: 12px;
                border: 1px solid #EAEAEA;
            }
        """)

        # paintEvent draws the card's shadow.
        self._card_shadow_key = None
        self._card_shadow = None

        card_layout = QVBoxLayout(self.card_frame)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)

        header_layout = QHBoxLayout()

        self.lbl_origin = QLabel(tr("main.label.original_auto"))
        self.lbl_origin.setStyleSheet("color: #909399; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        header_layout.addWidget(self.lbl_origin)

        header_layout.addStretch()

        self.btn_content_mode = QPushButton(tr("main.mode.auto"))
        self.btn_content_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_content_mode.clicked.connect(self.toggle_content_mode)
        header_layout.addWidget(self.btn_content_mode)

        self.lbl_model_name = QLabel()
        # Named, so the rule can't also reach the badge's tooltip, a label too.
        self.lbl_model_name.setObjectName("modelBadge")
        self.lbl_model_name.setStyleSheet("""
            #modelBadge {
                background-color: #F5F7FA;
                color: #909399;
                border: 1px solid #E4E7ED;
                border-radius: 6px;
                padding: 2px 6px;
                font-size: 11px;
                font-weight: 600;
            }
        """)
        self.refresh_api_status()
        header_layout.addWidget(self.lbl_model_name)

        self._settings_icon = create_settings_icon()
        self._update_icon = create_update_icon()
        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(self._settings_icon)
        self.btn_settings.setIconSize(QSize(20, 20))
        self.btn_settings.setFixedSize(32, 32)
        self.btn_settings.setAccessibleName(tr("main.settings"))
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setToolTip(
            tr("main.settings_tooltip", shortcut=shortcut_label(self.app_settings.shortcut))
        )
        self.btn_settings.setStyleSheet("""
            QPushButton {
                background-color: #F5F7FA;
                color: #606266;
                border: 1px solid #E4E7ED;
                border-radius: 6px;
                padding: 4px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                color: #8E44AD;
                border-color: #C39BD3;
            }
        """)
        self.btn_settings.clicked.connect(
            lambda _checked=False: self.on_settings_button_clicked()
        )
        header_layout.addWidget(self.btn_settings)

        self.lbl_dictionary_lang = QLabel(tr("main.language"))
        self.lbl_dictionary_lang.setObjectName("dictionaryLanguageLabel")
        self.lbl_dictionary_lang.setStyleSheet(
            "#dictionaryLanguageLabel { color: #909399; font-size: 11px; font-weight: 600; }"
        )
        self.lbl_dictionary_lang.setToolTip(tr("main.language.tooltip"))

        combo_style = DROPDOWN_STYLE + """
            QComboBox {
                background-color: #FAFAFA;
                color: #606266;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 2px 6px;
                font-size: 11px;
                min-height: 22px;
            }
            QComboBox:hover {
                border-color: #C0C4CC;
            }
        """

        self.cbo_dictionary_source = NoWheelComboBox()
        self.cbo_dictionary_source.setStyleSheet(combo_style)
        self.cbo_dictionary_source.setFixedWidth(82)
        self.cbo_dictionary_source.setToolTip(tr("main.language.source_tooltip"))
        for value, _label in DICTIONARY_SOURCE_LANGUAGES:
            self.cbo_dictionary_source.addItem(native_language_name(value), value)
        self.cbo_dictionary_source.currentIndexChanged.connect(self.on_dictionary_language_changed)

        self.lbl_dictionary_arrow = QLabel("→")
        self.lbl_dictionary_arrow.setStyleSheet("color: #C0C4CC; font-size: 12px;")

        self.cbo_dictionary_target = NoWheelComboBox()
        self.cbo_dictionary_target.setStyleSheet(combo_style)
        self.cbo_dictionary_target.setFixedWidth(82)
        self.cbo_dictionary_target.setToolTip(tr("main.language.target_tooltip"))
        for value, _label in DICTIONARY_TARGET_LANGUAGES:
            self.cbo_dictionary_target.addItem(native_language_name(value), value)
        self.cbo_dictionary_target.currentIndexChanged.connect(self.on_dictionary_language_changed)

        card_layout.addLayout(header_layout)

        self.txt_origin = InteractiveTextEdit()
        self.txt_origin.submit_signal.connect(self.start_manual_translation)
        self.txt_origin.selectionChanged.connect(lambda: self.on_text_selection_changed("origin"))
        self.txt_origin.setMaximumHeight(180)

        self.origin_font = QFont()
        self.origin_font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        self.origin_font.setPixelSize(14)
        self.txt_origin.setFont(self.origin_font)
        self.txt_origin.setStyleSheet("""
            QTextEdit {
                background-color: #F7F8FA;
                border: none;
                border-radius: 8px;
                padding: 4px 4px 4px 8px;
                color: #303133;
                selection-background-color: #D7BDE2;
                selection-color: #303133;
            }
        """)
        card_layout.addWidget(self.txt_origin)

        result_header_layout = QHBoxLayout()
        result_header_layout.setContentsMargins(0, 2, 0, 0)
        result_header_layout.setSpacing(8)

        self.lbl_result = QLabel(tr("main.label.auto_translation"))
        self.lbl_result.setStyleSheet("color: #8E44AD; font-size: 10px; font-weight: 700; letter-spacing: 1px; margin-top: 2px;")
        result_header_layout.addWidget(self.lbl_result)

        result_header_layout.addStretch()

        self.btn_back_translation = QPushButton(tr("main.back_translation"))
        self.btn_back_translation.setCheckable(True)
        self.btn_back_translation.setChecked(self.app_settings.back_translation)
        self.btn_back_translation.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back_translation.setToolTip(tr("main.back_translation.tooltip"))
        self.btn_back_translation.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #909399;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 600;
                min-height: 20px;
            }
            QPushButton:hover {
                color: #8E44AD;
                border-color: #C39BD3;
            }
            QPushButton:checked {
                background-color: #F4ECF7;
                color: #8E44AD;
                border-color: #C39BD3;
            }
        """)
        self.btn_back_translation.clicked.connect(self.toggle_back_translation)
        result_header_layout.addWidget(self.btn_back_translation)
        result_header_layout.addSpacing(4)
        result_header_layout.addWidget(self.lbl_dictionary_lang)
        result_header_layout.addWidget(self.cbo_dictionary_source)
        result_header_layout.addWidget(self.lbl_dictionary_arrow)
        result_header_layout.addWidget(self.cbo_dictionary_target)

        card_layout.addLayout(result_header_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: transparent; border-top: 1px dashed #E0E0E0; max-height: 1px; margin: 4px 0;")
        card_layout.addWidget(line)

        self.txt_result = InteractiveTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.selectionChanged.connect(lambda: self.on_text_selection_changed("result"))
        # No box of its own; its text lines up with the boxes' text.
        self.txt_result.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                border: none;
                padding: 0 4px 0 8px;
                selection-background-color: #D7BDE2;
                selection-color: #303133;
            }
        """)
        self.result_busy = BusyPlaceholder(self.txt_result)
        self.result_opacity_effect = QGraphicsOpacityEffect(self.txt_result)
        self.result_opacity_effect.setEnabled(False)
        self.txt_result.setGraphicsEffect(self.result_opacity_effect)
        card_layout.addWidget(self.txt_result)

        # Errors and stops show here, apart from the translated text.
        self.result_notice = QFrame()
        self.result_notice.setObjectName("resultNotice")
        notice_layout = QHBoxLayout(self.result_notice)
        notice_layout.setContentsMargins(10, 6, 6, 6)
        notice_layout.setSpacing(8)
        self.lbl_notice_icon = QLabel()
        self.lbl_notice_icon.setObjectName("resultNoticeIcon")
        self.lbl_notice_icon.setFixedSize(16, 16)
        self.lbl_notice_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice_layout.addWidget(self.lbl_notice_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lbl_notice = QLabel()
        self.lbl_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_notice.setWordWrap(True)
        self.lbl_notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        notice_layout.addWidget(self.lbl_notice, 1)
        self.btn_retry = QPushButton(tr("main.retry"))
        self.btn_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_retry.clicked.connect(self.retry_translation)
        notice_layout.addWidget(self.btn_retry, 0, Qt.AlignmentFlag.AlignVCenter)
        self.result_notice.hide()
        card_layout.addWidget(self.result_notice)

        # Source, translation and back-translation read top to bottom.
        self.back_translation_panel = QWidget()
        self.back_translation_panel.setObjectName("backTranslationPanel")
        self.back_translation_panel.setStyleSheet(
            "#backTranslationPanel { background-color: transparent; }"
        )
        back_layout = QVBoxLayout(self.back_translation_panel)
        back_layout.setContentsMargins(0, 2, 0, 0)
        back_layout.setSpacing(6)
        back_header_layout = QHBoxLayout()
        self.lbl_back_translation = QLabel(tr("main.label.back_translation"))
        self.lbl_back_translation.setStyleSheet("color: #909399; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        back_header_layout.addWidget(self.lbl_back_translation)
        back_header_layout.addStretch()
        self.btn_stop_back_translation = QPushButton(tr("main.stop_back_translation"))
        self.btn_stop_back_translation.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop_back_translation.setToolTip(
            tr("main.stop_back_translation.tooltip")
        )
        self.btn_stop_back_translation.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #909399;
                border: none;
                padding: 0 2px;
                font-size: 11px;
            }
            QPushButton:hover {
                color: #F56C6C;
            }
        """)
        self.btn_stop_back_translation.clicked.connect(self.stop_back_translation)
        self.btn_stop_back_translation.hide()
        back_header_layout.addWidget(self.btn_stop_back_translation)
        back_layout.addLayout(back_header_layout)
        self.txt_back_translation = InteractiveTextEdit()
        self.txt_back_translation.setReadOnly(True)
        self.txt_back_translation.setMaximumHeight(150)
        back_font = QFont(self.origin_font)
        back_font.setPixelSize(13)
        self.txt_back_translation.setFont(back_font)
        self.txt_back_translation.setStyleSheet("""
            QTextEdit {
                background-color: #FAF7FC;
                border: none;
                border-radius: 8px;
                padding: 4px 4px 4px 8px;
                color: #606266;
                selection-background-color: #D7BDE2;
                selection-color: #303133;
            }
        """)
        back_layout.addWidget(self.txt_back_translation)
        self.back_translation_panel.hide()
        card_layout.addWidget(self.back_translation_panel)

        # Mouse presses on the text itself; wheel and scroll bars stay out.
        self.txt_origin.viewport().installEventFilter(self)
        self.txt_result.viewport().installEventFilter(self)

        self.btn_align_selection = QPushButton(self.card_frame)
        self.btn_align_selection.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_align_selection.setFixedHeight(26)
        self.btn_align_selection.setStyleSheet("""
            QPushButton {
                background-color: #8E44AD;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 3px 10px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #9B59B6;
            }
        """)
        self.btn_align_selection.clicked.connect(self.highlight_selected_counterpart)
        self.btn_align_selection.hide()

        main_layout.addWidget(self.card_frame)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 10, 0, 0)
        btn_layout.setSpacing(10)

        self.btn_edit_origin = QPushButton(tr("main.edit"))
        self.btn_edit_origin.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_origin.setToolTip(tr("main.edit.tooltip"))
        self.btn_edit_origin.clicked.connect(self.edit_selected_text)
        btn_layout.addWidget(self.btn_edit_origin)

        self.btn_clear_origin = QPushButton(tr("main.clear"))
        self.btn_clear_origin.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_origin.clicked.connect(self.clear_manual_origin)
        self.btn_clear_origin.setStyleSheet("""
            QPushButton {
                background-color: #F2F3F5;
                color: #606266;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 6px 16px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #E4E7ED;
                color: #303133;
            }
        """)
        self.btn_edit_origin.setStyleSheet(self.btn_clear_origin.styleSheet())
        btn_layout.addWidget(self.btn_clear_origin)
        btn_layout.addStretch(1)

        self.btn_ocr_clipboard = QPushButton(tr("main.ocr"))
        self.btn_ocr_clipboard.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ocr_clipboard.setToolTip(tr("main.ocr.tooltip"))
        self.btn_ocr_clipboard.clicked.connect(self.start_clipboard_ocr)
        self.btn_ocr_clipboard.setStyleSheet("""
            QPushButton {
                background-color: #F2F3F5;
                color: #606266;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 6px 14px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #E4E7ED;
                color: #303133;
            }
            QPushButton:disabled {
                background-color: #F2F3F5;
                color: #C0C4CC;
            }
        """)
        btn_layout.addWidget(self.btn_ocr_clipboard)

        self.btn_history = QPushButton(tr("main.history"))
        self.btn_history.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_history.clicked.connect(self.show_history_menu)
        self.btn_history.setStyleSheet("""
            QPushButton {
                background-color: #F2F3F5;
                color: #606266;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 6px 14px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #E4E7ED;
                color: #303133;
            }
        """)
        btn_layout.addWidget(self.btn_history)

        self.btn_translate = QPushButton(tr("main.translate"))
        self.btn_translate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_translate.clicked.connect(self.start_manual_translation)
        self.btn_translate.setStyleSheet("""
            QPushButton {
                background-color: #8E44AD;
                color: white;
                border: 1px solid #8E44AD;
                border-radius: 6px;
                padding: 6px 16px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #9B59B6;
                border-color: #9B59B6;
            }
            QPushButton:pressed {
                background-color: #7D3C98;
                border-color: #7D3C98;
            }
            QPushButton:disabled {
                background-color: #D7BDE2;
                color: #FFFFFF;
                border-color: #D7BDE2;
            }
        """)
        btn_layout.addWidget(self.btn_translate)

        self.btn_stop = QPushButton(tr("main.stop"))
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.setToolTip(tr("main.stop.tooltip"))
        self.btn_stop.clicked.connect(self.stop_translation)
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                color: #F56C6C;
                border: 1px solid #FBC4C4;
                border-radius: 6px;
                padding: 6px 16px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #FEF0F0;
                border-color: #F56C6C;
            }
        """)
        self.btn_stop.hide()
        btn_layout.addWidget(self.btn_stop)

        self.btn_copy = QPushButton(tr("main.copy"))
        self.btn_copy.setEnabled(False)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #F4ECF7;
                color: #8E44AD;
                border: 1px solid #E3D0EC;
                border-radius: 6px;
                padding: 6px 16px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #EBDDF2;
                border-color: #D7BDE2;
            }
            QPushButton:pressed {
                background-color: #E3D0EC;
            }
            QPushButton:disabled {
                background-color: #F8F4FA;
                color: #C9A9D8;
                border-color: #EFE4F4;
            }
        """)
        btn_layout.addWidget(self.btn_copy)

        self.btn_hide = QPushButton(tr("main.hide"))
        self.btn_hide.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_hide.clicked.connect(lambda _checked=False: self.hide())
        self.btn_hide.setStyleSheet("""
            QPushButton {
                background-color: #F2F3F5;
                color: #606266;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 6px 16px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #E4E7ED;
                color: #303133;
            }
        """)
        btn_layout.addWidget(self.btn_hide)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)
        self.setMinimumSize(420, 300)
        self.match_translate_and_stop_widths()
        use_modern_tooltips(self)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setWindowOpacity(1.0)

    def setup_result_format(self):
        if hasattr(self, "btn_align_selection"):
            self.reset_alignment_ui()
        if hasattr(self, "back_translation_panel"):
            self.reset_back_translation()
        if hasattr(self, "result_notice"):
            self.hide_result_notice()
        if hasattr(self, "result_busy"):
            self.result_busy.stop()

        self.result_opacity_effect.setEnabled(False)
        self.live_render_timer.stop()
        self._live_render_due = 0.0
        self.txt_result.clear()

        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(15)

        self.result_char_fmt = QTextCharFormat()
        self.result_char_fmt.setFont(font)
        self.result_char_fmt.setForeground(QColor("#2c3e50"))

    def apply_markdown_document_style(self, widget, source_name: str):
        text_color = "#2c3e50" if source_name == "result" else "#606266"
        if source_name == "origin":
            font_pixel_size = 13 if self.source_mode == "snipdo" else 14
        elif source_name == "back":
            font_pixel_size = 13
        else:
            font_pixel_size = 15
        font_size = f"{font_pixel_size}px"
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(font_pixel_size)
        widget.setFont(font)
        widget.document().setDefaultFont(font)
        widget.document().setIndentWidth(14)
        widget.document().setDefaultStyleSheet(f"""
            body {{
                color: {text_color};
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: {font_size};
                line-height: 1.18;
            }}
            h1, h2, h3, h4, h5, h6 {{
                color: #303133;
                font-weight: 600;
                margin-top: 10px;
                margin-bottom: 6px;
            }}
            h1 {{
                font-size: 20px;
            }}
            h2 {{
                font-size: 18px;
            }}
            h3 {{
                font-size: 16px;
            }}
            h4, h5, h6 {{
                font-size: 15px;
            }}
            p {{
                margin-top: 0;
                margin-bottom: 8px;
            }}
            ul, ol {{
                margin-top: 4px;
                margin-bottom: 8px;
                margin-left: 10px;
                padding-left: 10px;
            }}
            blockquote {{
                color: #606266;
                border-left: 3px solid #DCDFE6;
                margin-left: 0;
                padding-left: 8px;
            }}
            code, pre {{
                background-color: #F5F7FA;
                color: #303133;
                font-family: Consolas, "Cascadia Mono", monospace;
            }}
            table {{
                border-collapse: collapse;
            }}
            th, td {{
                border: 1px solid #DCDFE6;
                padding: 4px 6px;
            }}
        """)

    def compact_markdown_list_indents(self, widget):
        document = widget.document()
        block = document.firstBlock()

        while block.isValid():
            text_list = block.textList()
            if text_list is not None:
                list_format = text_list.format()
                indent = max(1, min(list_format.indent(), 2))
                if list_format.indent() != indent:
                    list_format.setIndent(indent)
                    text_list.setFormat(list_format)
            block = block.next()

    def apply_markdown_block_formats(self, widget, source_name: str):
        document = widget.document()
        text_color = QColor("#2C3E50" if source_name == "result" else "#606266")
        heading_sizes = {
            1: 20,
            2: 18,
            3: 16,
            4: 15,
            5: 15,
            6: 15,
        }
        block = document.firstBlock()

        while block.isValid():
            block_format = block.blockFormat()
            # Display formulas have already set their own line height.
            if (
                block_format.lineHeightType()
                == QTextBlockFormat.LineHeightTypes.SingleHeight.value
            ):
                block_format.setLineHeight(
                    140,
                    QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
                )

            heading_level = block_format.headingLevel()
            if heading_level:
                block_format.setTopMargin(10)
                block_format.setBottomMargin(6)

            block_cursor = QTextCursor(block)
            block_cursor.setBlockFormat(block_format)

            if heading_level:
                heading_fragments = []
                fragment_iterator = block.begin()
                while not fragment_iterator.atEnd():
                    fragment = fragment_iterator.fragment()
                    # A formula image keeps the font its baseline was set by.
                    if fragment.isValid() and not fragment.charFormat().isImageFormat():
                        heading_fragments.append((
                            fragment.position(),
                            fragment.length(),
                            QTextCharFormat(fragment.charFormat()),
                        ))
                    fragment_iterator += 1

                for position, length, heading_format in heading_fragments:
                    heading_format.clearProperty(QTextFormat.Property.FontSizeAdjustment)
                    heading_format.setProperty(
                        QTextFormat.Property.FontPixelSize,
                        heading_sizes.get(heading_level, 15),
                    )
                    heading_format.setFontWeight(QFont.Weight.DemiBold)
                    heading_format.setForeground(text_color)

                    fragment_cursor = QTextCursor(document)
                    fragment_cursor.setPosition(position)
                    fragment_cursor.setPosition(
                        position + length,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    fragment_cursor.setCharFormat(heading_format)
            else:
                block_cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                text_format = QTextCharFormat()
                text_format.setForeground(text_color)
                block_cursor.mergeCharFormat(text_format)

            block = block.next()

    def render_markdown_text(
        self,
        widget,
        markdown_text: str,
        source_name: str,
        *,
        preview: bool = False,
    ) -> bool:
        """Render Markdown; a preview shows formulas as source instead of images."""
        markdown_text = (markdown_text or "").strip()
        if not markdown_text:
            widget.clear()
            return True

        try:
            self.apply_markdown_document_style(widget, source_name)
            plain_text = (
                is_latex_text(markdown_text)
                and not is_markdown_structured_text(markdown_text)
            )
            document_text = markdown_text
            formula_fragments = []
            if source_name == "result":
                document_text, formula_fragments = (
                    prepare_latex_math_for_document(
                        markdown_text,
                        paragraph_break="\n" if plain_text else "\n\n",
                    )
                )

            if plain_text:
                widget.setPlainText(document_text)
            else:
                widget.setMarkdown(document_text)

            # One edit block: outside it Qt lays the whole document out again
            # after every block format, so long texts took seconds to render.
            edit_cursor = QTextCursor(widget.document())
            edit_cursor.beginEditBlock()
            try:
                if not plain_text:
                    self.compact_markdown_list_indents(widget)
                if formula_fragments and preview:
                    show_math_fragment_sources(widget, formula_fragments)
                elif formula_fragments:
                    formula_stats = render_math_fragments_in_document(
                        widget,
                        formula_fragments,
                        font_pixel_size=15,
                        color="#2c3e50",
                    )
                    if formula_stats.fallback:
                        log(
                            "[LaTeX] formula render fallback "
                            f"count={formula_stats.fallback}"
                        )
                self.apply_markdown_block_formats(widget, source_name)
            finally:
                edit_cursor.endEditBlock()
            widget.moveCursor(QTextCursor.MoveOperation.Start)
            return True
        except Exception as e:
            log(f"[Markdown] render failed for {source_name}: {e}")
            widget.setPlainText(markdown_text)
            return False

    def load_translation_history(self):
        try:
            entries = load_history(self.app_paths.history_path)
            record_event(AppEvent.HISTORY_LOADED)
            return entries
        except (HistoryDataError, OSError):
            record_event(AppEvent.HISTORY_UNAVAILABLE)
            return []

    def save_translation_history(self):
        try:
            save_history_atomic(
                self.app_paths.history_path,
                self.translation_history[:MAX_HISTORY_ITEMS],
            )
        except (HistoryDataError, OSError):
            record_event(AppEvent.HISTORY_UNAVAILABLE)

    def history_preview(self, text: str, limit: int = 54) -> str:
        preview = re.sub(r"\s+", " ", (text or "").strip())
        if len(preview) > limit:
            preview = preview[:limit - 3].rstrip() + "..."
        return preview or "(empty)"

    def add_history_entry(self, source_text: str, result_text: str, mode: str):
        source_text = (source_text or "").strip()
        result_text = (result_text or "").strip()
        if not source_text or not result_text:
            return

        entry = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode or "auto",
            "source": source_text,
            "result": result_text,
        }

        self.translation_history = [
            item for item in self.translation_history
            if item.get("source") != source_text or item.get("result") != result_text
        ]
        self.translation_history.insert(0, entry)
        self.translation_history = self.translation_history[:MAX_HISTORY_ITEMS]
        self.save_translation_history()

    def show_history_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)

        if not self.translation_history:
            empty_action = QAction(tr("main.history.empty"), self)
            empty_action.setEnabled(False)
            menu.addAction(empty_action)
        else:
            for entry in self.translation_history[:12]:
                when = history_time_label(entry.get("time", ""))
                title = f"{when}  {self.history_preview(entry.get('source', ''))}"
                action = QAction(title, self)
                action.triggered.connect(lambda _checked=False, item=entry: self.load_history_entry(item))
                menu.addAction(action)

            menu.addSeparator()
            clear_action = QAction(tr("main.history.clear"), self)
            clear_action.triggered.connect(self.clear_translation_history)
            menu.addAction(clear_action)

        menu.exec(self.btn_history.mapToGlobal(self.btn_history.rect().bottomLeft()))

    def clear_translation_history(self):
        self.translation_history = []
        self.save_translation_history()

    def load_history_entry(self, entry):
        source_text = (entry.get("source") or "").strip()
        result_text = (entry.get("result") or "").strip()
        if not source_text and not result_text:
            return

        self.cancel_current_translation()
        self.source_mode = "manual"
        self.apply_manual_mode_ui(reset_content=True)
        self.txt_origin.setPlainText(source_text)
        self.original_paragraphs = [p.strip() for p in re.split(r'\n+', normalize_newlines(source_text)) if p.strip()]
        self.active_translation_text = source_text
        self.active_translation_mode = entry.get("mode") or "auto"
        self.full_translation = result_text
        self.setup_result_format()
        if result_text:
            self.render_markdown_text(self.txt_result, result_text, "result")
            self.btn_copy.setEnabled(True)
            self.start_back_translation()
        self.force_show_window()

    def ensure_api_key(self, *, allow_prompt: bool = True) -> bool:
        provider = self.app_settings.resolved_provider()
        if not provider.model:
            if allow_prompt:
                show_warning(
                    self, tr("warning.no_model.title"), tr("warning.no_model.text")
                )
            return False
        if (
            client is not None
            and api_runtime.provider == provider
        ):
            return True
        if not allow_prompt:
            record_event(AppEvent.CREDENTIAL_MISSING)
            return False

        self.force_show_window()
        api_key, ok = ask_text(
            self,
            tr("prompt.api_key.title", provider=provider_name(provider.provider_id, short=True)),
            tr("prompt.api_key.text", provider=provider_name(provider.provider_id)),
            password=True,
        )

        config = self.app_settings.api_configs.get(provider.provider_id)
        if ok and configure_api_client(
            api_key, provider.provider_id,
            **({"config": config} if config is not None else {}),
        ):
            target_store = credential_store_for_window(
                self,
                provider.provider_id,
            )
            try:
                if target_store is None or not target_store.write(api_key):
                    record_event(AppEvent.CREDENTIAL_UNAVAILABLE)
                else:
                    self.credential_store = target_store
                    record_event(AppEvent.CREDENTIAL_AVAILABLE)
            except Exception:
                # The key remains available for this process only.  It is never
                # written to a plaintext fallback.
                record_event(AppEvent.CREDENTIAL_UNAVAILABLE)
            return True

        record_event(AppEvent.CREDENTIAL_MISSING)
        return False

    def set_result_message(self, message: str, *, retry: bool = False):
        """Clear the result and explain why it is empty."""
        self.setup_result_format()
        self.show_result_notice(message, retry=retry)

    def show_result_notice(self, message: str, *, error: bool = True, retry: bool = False):
        self.result_notice.setStyleSheet(
            RESULT_ERROR_NOTICE_STYLE if error else RESULT_INFO_NOTICE_STYLE
        )
        self.lbl_notice_icon.setText("!" if error else "i")
        self.lbl_notice.setText(message)
        self.btn_retry.setVisible(retry)
        self.result_notice.show()

    def hide_result_notice(self):
        self.result_notice.hide()

    def retry_translation(self):
        self.hide_result_notice()
        if self.source_mode == "manual":
            self.start_manual_translation()
        else:
            self.rerun_snipdo_translation()

    def start_clipboard_ocr(self):
        try:
            log("[UI] start_clipboard_ocr")
            self.cancel_current_translation()
            self.source_mode = "manual"
            self.apply_manual_mode_ui(reset_content=True)
            self.force_show_window()

            image_data_url = clipboard_image_to_data_url()
            self.start_ocr(image_data_url, result_source_mode="manual")
        except Exception as e:
            log(f"[UI] start_clipboard_ocr error: {e}")
            self.set_result_message(tr("message.ocr_error", error=e))

    def start_image_file_ocr(
        self,
        file_path: str,
        *,
        allow_key_prompt: bool = True,
    ) -> ReceiveResult:
        log(f"[UI] start_image_file_ocr file={file_path}")

        provider = api_provider_for_window(self)
        if not provider.supports_vision:
            self.force_show_window()
            self.set_result_message(
                tr(
                    "message.ocr_unsupported",
                    provider=provider_name(provider.provider_id, short=True),
                )
            )
            record_event(AppEvent.OCR_FAILED)
            return ReceiveResult.reject(RejectionReason.STARTUP_FAILED)

        # Deletion belongs exclusively to the launching process after an
        # accepted ACK.  The primary must first own an in-memory copy.
        if self.ocr_thread and self.ocr_thread.isRunning():
            record_event(AppEvent.OCR_BUSY)
            return ReceiveResult.reject(RejectionReason.BUSY)
        if not self.ensure_api_key(allow_prompt=allow_key_prompt):
            self.set_result_message(tr("message.ocr_no_key"))
            return ReceiveResult.reject(RejectionReason.MISSING_KEY)

        try:
            image_data_url = image_file_to_data_url(file_path)
        except Exception:
            self.force_show_window()
            self.set_result_message(
                tr("message.ocr_error", error=tr("error.cannot_read_image"))
            )
            record_event(AppEvent.OCR_FAILED)
            return ReceiveResult.reject(RejectionReason.NOT_OWNED)

        started = self.start_ocr(
            image_data_url,
            result_source_mode="snipdo",
            allow_key_prompt=allow_key_prompt,
        )
        return (
            ReceiveResult.accept()
            if started
            else ReceiveResult.reject(RejectionReason.STARTUP_FAILED)
        )

    def start_ocr(
        self,
        image_data_url: str,
        result_source_mode: str,
        *,
        allow_key_prompt: bool = True,
    ):
        if self.ocr_thread and self.ocr_thread.isRunning():
            record_event(AppEvent.OCR_BUSY)
            return False

        provider = api_provider_for_window(self)
        if not provider.supports_vision:
            self.set_result_message(
                tr(
                    "message.ocr_unsupported",
                    provider=provider_name(provider.provider_id, short=True),
                )
            )
            record_event(AppEvent.OCR_FAILED)
            return False

        if not self.ensure_api_key(allow_prompt=allow_key_prompt):
            self.set_result_message(tr("message.ocr_no_key"))
            return False

        self.cancel_current_translation()
        self.ocr_result_source_mode = result_source_mode
        self.full_translation = ""
        self.original_paragraphs = []
        self.pending_snipdo_text = ""

        self.setup_result_format()
        self.txt_origin.clear()
        self.txt_origin.setReadOnly(True)
        self.show_heading_labels("ocr")
        self.btn_copy.setText(tr("main.ocr.busy"))
        self.btn_copy.setEnabled(False)
        self.btn_edit_origin.setEnabled(False)

        if self.source_mode == "manual":
            self.btn_clear_origin.setEnabled(False)
            self.btn_translate.setEnabled(False)
            self.btn_translate.setText(tr("main.ocr.busy"))
            self.btn_ocr_clipboard.setEnabled(False)

        self.result_busy.start(tr("message.ocr_running"))
        self.ensure_large_window_size()
        self.force_show_window()

        try:
            self.ocr_thread = OcrThread(image_data_url)
            self.ocr_thread.finished.connect(self.on_ocr_finished)
            self.ocr_thread.start()
        except Exception:
            self.ocr_thread = None
            self.set_result_message(
                tr("message.ocr_error", error=tr("error.cannot_start"))
            )
            record_event(AppEvent.OCR_FAILED)
            return False

        record_event(AppEvent.OCR_STARTED)
        return True

    def on_ocr_finished(self, success: bool, extracted_text: str, error_msg: str):
        log(f"[UI] on_ocr_finished success={success}, error={error_msg}")
        self.result_busy.stop()

        if self.source_mode == "manual":
            self.btn_clear_origin.setEnabled(True)
            self.btn_translate.setEnabled(True)
            self.btn_translate.setText(tr("main.translate"))
            self.btn_ocr_clipboard.setEnabled(True)

        self.btn_copy.setText(tr("main.copy"))
        self.btn_copy.setEnabled(False)

        if not success:
            record_event(AppEvent.OCR_FAILED)
            if error_msg != CANCELLED_ERROR:
                self.set_result_message(tr("message.ocr_error", error=error_msg))
            return

        ocr_text = normalize_newlines(extracted_text).strip()
        if not ocr_text:
            record_event(AppEvent.OCR_FAILED)
            self.set_result_message(
                tr("message.ocr_error", error=tr("error.no_text_found"))
            )
            return

        record_event(AppEvent.OCR_COMPLETED)

        if self.ocr_result_source_mode == "snipdo":
            QTimer.singleShot(60, lambda text=ocr_text: self.handle_new_request(text))
            return

        self.source_mode = "manual"
        self.apply_manual_mode_ui(reset_content=True)
        self.txt_origin.setPlainText(ocr_text)
        self.force_show_window()
        QTimer.singleShot(60, self.start_manual_translation)

    def edit_selected_text(self):
        if self.source_mode != "snipdo":
            return
        if self.ocr_thread and self.ocr_thread.isRunning():
            return

        source_text = self.pending_snipdo_text or "\n".join(self.original_paragraphs)
        if not source_text.strip():
            return

        self.cancel_current_translation()
        self.flush_live_translation()
        self.reset_back_translation()
        self.apply_manual_mode_ui(reset_content=False)
        # Use the source, not the rendered document, to preserve Markdown/LaTeX.
        self.reset_origin_editor()
        self.txt_origin.setPlainText(source_text)
        # The previous result recedes while the source is being edited.
        self.result_opacity_effect.setOpacity(1.0)
        self.result_opacity_effect.setEnabled(True)
        fade_opacity(self.result_opacity_effect, 0.45)
        self.btn_clear_origin.setEnabled(True)
        self.btn_translate.setEnabled(True)
        self.btn_translate.setText(tr("main.translate"))
        self.btn_copy.setText(tr("main.copy"))
        self.btn_copy.setEnabled(False)
        self.txt_origin.setFocus()

    def clear_manual_origin(self):
        if self.source_mode != "manual":
            return

        self.cancel_current_translation()
        self.reset_origin_editor()
        self.original_paragraphs = []
        self.full_translation = ""
        self.setup_result_format()
        self.btn_translate.setEnabled(True)
        self.btn_translate.setText(tr("main.translate"))
        self.btn_copy.setText(tr("main.copy"))
        self.btn_copy.setEnabled(False)
        self.txt_origin.setFocus()

    def reset_origin_editor(self):
        """Empty the input and drop formatting left by rendered or pasted text."""
        # QTextEdit.clear() and setPlainText() keep the cursor's character format.
        self.txt_origin.clear()
        self.txt_origin.setCurrentCharFormat(QTextCharFormat())
        self.txt_origin.setFont(self.origin_font)
        document = self.txt_origin.document()
        document.setDefaultFont(self.origin_font)
        document.setDefaultStyleSheet("")

    def current_dictionary_languages(self):
        source_lang = self.cbo_dictionary_source.currentData() or "auto"
        target_lang = self.cbo_dictionary_target.currentData() or "default"
        self.dictionary_source_lang = source_lang
        self.dictionary_target_lang = target_lang
        return source_lang, target_lang

    def set_dictionary_language_controls_visible(self, visible: bool):
        # Some languages leave the caption empty to make room for the heading.
        self.lbl_dictionary_lang.setVisible(visible and bool(self.lbl_dictionary_lang.text()))
        self.cbo_dictionary_source.setVisible(visible)
        self.lbl_dictionary_arrow.setVisible(visible)
        self.cbo_dictionary_target.setVisible(visible)

    def selected_translation_mode(self) -> str:
        _source_lang, target_lang = self.current_dictionary_languages()
        return translation_mode_for_target_language(target_lang)

    def selected_translation_label(self, mode: str) -> str:
        _source_lang, target_lang = self.current_dictionary_languages()
        return translation_label_for_target_language(mode, target_lang)

    def selected_origin_label(self) -> str:
        source_lang, _target_lang = self.current_dictionary_languages()
        return tr(
            "main.label.original_auto" if source_lang == "auto" else "main.label.original"
        )

    def show_heading_labels(self, mode: str):
        """Label both panes for "ocr", "dictionary" or a translation direction."""
        self.heading_mode = mode
        # Dictionary entries and OCR text aren't back-translated.
        self.btn_back_translation.setVisible(mode not in ("dictionary", "ocr"))
        if mode == "ocr":
            self.lbl_origin.setText(tr("main.label.ocr_image"))
            self.lbl_result.setText(tr("main.label.ocr"))
        elif mode == "dictionary":
            self.lbl_origin.setText(tr("main.label.original"))
            self.lbl_result.setText(tr("main.label.dictionary"))
        else:
            self.lbl_origin.setText(self.selected_origin_label())
            self.lbl_result.setText(self.selected_translation_label(mode))

    def update_content_mode_button_text(self):
        dictionary = self.content_mode_override == "dictionary"
        self.btn_content_mode.setText(
            tr("main.mode.dictionary" if dictionary else "main.mode.auto")
        )
        # Grey while it decides by itself; the accent while Dictionary is forced.
        color, hover = ("#8E44AD", "#9B59B6") if dictionary else ("#909399", "#8E44AD")
        self.btn_content_mode.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {color};
                border: none;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                color: {hover};
            }}
        """)

    def match_translate_and_stop_widths(self):
        """Stop takes Translate's place at its width, so no other button moves."""
        text = self.btn_translate.text()
        self.btn_translate.setText(tr("main.translate"))
        self.btn_translate.ensurePolished()
        self.btn_stop.ensurePolished()
        width = max(self.btn_translate.sizeHint().width(), self.btn_stop.sizeHint().width())
        self.btn_translate.setText(text)
        self.btn_translate.setMinimumWidth(width)
        self.btn_stop.setMinimumWidth(width)

    def resolve_effective_mode(self, text: str) -> str:
        if self.content_mode_override == "dictionary":
            return "dictionary"
        source_lang, target_lang = self.current_dictionary_languages()
        # Every selection change asks again; rescanning a very long text on
        # each mouse move made dragging a selection sluggish.
        key = (text, source_lang, target_lang)
        cached = getattr(self, "_effective_mode_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        if is_dictionary_mode(text):
            mode = "dictionary"
        else:
            mode = resolve_auto_translation_mode(text, source_lang, target_lang)
        self._effective_mode_cache = (key, mode)
        return mode

    def rerun_snipdo_translation(self):
        if self.source_mode != "snipdo":
            return

        total_text = self.pending_snipdo_text.strip() or "\n".join(self.original_paragraphs).strip()
        if not total_text:
            self.apply_snipdo_mode_ui()
            return

        effective_mode = self.resolve_effective_mode(total_text)
        self.cancel_current_translation()
        self.full_translation = ""
        self.setup_result_format()
        self.btn_copy.setText(tr("main.looking_up" if effective_mode == "dictionary" else "main.translating"))
        self.btn_copy.setEnabled(False)
        self.apply_snipdo_mode_ui()
        self.start_translation(total_text, effective_mode)

    def toggle_content_mode(self):
        order = ["auto", "dictionary"]
        try:
            idx = order.index(self.content_mode_override)
        except ValueError:
            idx = 0

        self.content_mode_override = order[(idx + 1) % len(order)]
        self.update_content_mode_button_text()

        if self.source_mode == "snipdo":
            self.rerun_snipdo_translation()
        else:
            self.apply_manual_mode_ui(reset_content=False)
            if self.txt_origin.toPlainText().strip():
                self.start_manual_translation()

    def on_dictionary_language_changed(self, *_args):
        self.current_dictionary_languages()

        if self.source_mode == "snipdo":
            self.rerun_snipdo_translation()
        else:
            self.apply_manual_mode_ui(reset_content=False)
            if self.txt_origin.toPlainText().strip():
                self.start_manual_translation()

    # ---------- Selection alignment ----------
    def reset_alignment_ui(self, clear_highlights=True):
        if hasattr(self, "align_thread"):
            self.cancel_current_alignment()
        self.alignment_selection_source = None
        self.alignment_selection_range = None
        self.alignment_target_widget = None
        self.alignment_selected_text = ""
        self.alignment_selected_sentence = ""
        self.alignment_selection_is_sentence = False
        if hasattr(self, "btn_align_selection"):
            self.btn_align_selection.setEnabled(True)
            self.btn_align_selection.hide()
        if clear_highlights:
            self.clear_alignment_highlights()

    def clear_alignment_highlights(self):
        if hasattr(self, "txt_origin"):
            self.txt_origin.setExtraSelections([])
        if hasattr(self, "txt_result"):
            self.txt_result.setExtraSelections([])
        self.alignment_highlight_source = None

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if watched is self.txt_origin.viewport():
                self.dismiss_alignment_highlight(self.txt_result)
            elif watched is self.txt_result.viewport():
                self.dismiss_alignment_highlight(self.txt_origin)
        return super().eventFilter(watched, event)

    def dismiss_alignment_highlight(self, other_widget):
        """A click in either text drops the located pair; scrolling keeps it."""
        if self.align_thread is not None and self.align_thread.isRunning():
            # Do not block the click; a late answer finds no target and is dropped.
            self.align_thread.request_stop()
        self.alignment_target_widget = None
        self.btn_align_selection.setEnabled(True)
        self.btn_align_selection.hide()
        self.clear_alignment_highlights()

        # The clicked text clears its own selection; the other one keeps its view.
        cursor = other_widget.textCursor()
        if cursor.hasSelection():
            scroll_bar = other_widget.verticalScrollBar()
            position = scroll_bar.value()
            cursor.clearSelection()
            other_widget.setTextCursor(cursor)
            scroll_bar.setValue(position)

    def alignment_feature_enabled(self):
        if self.source_mode != "snipdo":
            # History entries and finished manual translations, until the
            # input no longer holds the text that was translated.
            return (
                self.trans_thread is None
                and bool(self.full_translation.strip())
                and self.active_translation_mode != "dictionary"
                and "".join(self.txt_origin.toPlainText().split())
                == "".join(self.active_translation_text.split())
            )

        total_text = self.pending_snipdo_text.strip() or "\n".join(self.original_paragraphs).strip()
        if not total_text or not self.full_translation.strip():
            return False

        return self.resolve_effective_mode(total_text) != "dictionary"

    def on_text_selection_changed(self, source_name):
        if not hasattr(self, "btn_align_selection"):
            return

        if not self.alignment_feature_enabled():
            self.reset_alignment_ui()
            return

        source_widget = self.txt_origin if source_name == "origin" else self.txt_result
        cursor = source_widget.textCursor()
        if not cursor.hasSelection():
            self.btn_align_selection.hide()
            return

        start = min(cursor.selectionStart(), cursor.selectionEnd())
        end = max(cursor.selectionStart(), cursor.selectionEnd())
        if start == end:
            self.btn_align_selection.hide()
            return

        self.clear_alignment_highlights()
        self.alignment_selection_source = source_name
        self.alignment_selection_range = (start, end)

        self.btn_align_selection.setText(
            tr("main.locate.translation" if source_name == "origin" else "main.locate.original")
        )
        self.btn_align_selection.adjustSize()

        rect = source_widget.cursorRect(cursor)
        global_pos = source_widget.viewport().mapToGlobal(rect.topRight())
        pos = self.card_frame.mapFromGlobal(global_pos)

        margin = 6
        x = min(max(margin, pos.x() + 8), max(margin, self.card_frame.width() - self.btn_align_selection.width() - margin))
        y = min(max(margin, pos.y() - self.btn_align_selection.height() - 8), max(margin, self.card_frame.height() - self.btn_align_selection.height() - margin))

        appearing = self.btn_align_selection.isHidden()
        self.btn_align_selection.move(x, y)
        self.btn_align_selection.show()
        self.btn_align_selection.raise_()
        if appearing:
            fade_in(self.btn_align_selection)

    def find_text_range(self, text, snippet, left_context="", right_context="", occurrence_index=0):
        ranges = self.find_text_ranges(text, snippet)
        if not ranges:
            return None

        if len(ranges) == 1:
            return ranges[0]

        context_range = self.best_context_range(text, ranges, left_context, right_context)
        if context_range:
            return context_range

        try:
            occurrence_index = int(occurrence_index)
        except Exception:
            occurrence_index = 0

        if 1 <= occurrence_index <= len(ranges):
            return ranges[occurrence_index - 1]

        return None

    def find_text_ranges(self, text, snippet):
        snippet = (snippet or "").strip()
        if not snippet:
            return []

        for candidate in self.literal_match_candidates(snippet):
            ranges = self.exact_text_ranges(text, candidate)
            if ranges:
                return ranges

        ranges = self.exact_text_ranges(text, snippet)
        if ranges:
            return ranges

        normalized_text, text_index_map = self.normalized_with_index_map(text)
        normalized_snippet, _snippet_index_map = self.normalized_with_index_map(snippet)
        if not normalized_snippet:
            return []

        ranges = []
        search_from = 0
        while True:
            normalized_start = normalized_text.find(normalized_snippet, search_from)
            if normalized_start < 0:
                break
            normalized_end = normalized_start + len(normalized_snippet) - 1
            ranges.append((text_index_map[normalized_start], text_index_map[normalized_end] + 1))
            search_from = normalized_start + max(1, len(normalized_snippet))
        return ranges

    def exact_text_ranges(self, text, snippet):
        if not snippet:
            return []

        ranges = []
        search_from = 0
        while True:
            start = text.find(snippet, search_from)
            if start < 0:
                break
            end = start + len(snippet)
            ranges.append((start, end))
            search_from = end
        return ranges

    def best_context_range(self, text, ranges, left_context="", right_context=""):
        left_norm = self.normalized_with_index_map(left_context or "")[0]
        right_norm = self.normalized_with_index_map(right_context or "")[0]
        if not left_norm and not right_norm:
            return None

        best_range = None
        best_score = 0
        for start, end in ranges:
            before_norm = self.normalized_with_index_map(text[:start])[0]
            after_norm = self.normalized_with_index_map(text[end:])[0]
            score = 0

            if left_norm:
                if before_norm.endswith(left_norm):
                    score += len(left_norm) * 3
                elif left_norm in before_norm:
                    score += len(left_norm)

            if right_norm:
                if after_norm.startswith(right_norm):
                    score += len(right_norm) * 3
                elif right_norm in after_norm:
                    score += len(right_norm)

            if score > best_score:
                best_score = score
                best_range = (start, end)

        return best_range

    def literal_match_candidates(self, snippet):
        candidates = []
        stripped = snippet.strip()
        if stripped:
            candidates.append(stripped)

        unquoted = stripped.strip("\"'`“”‘’")
        quote_pairs = [
            ('"', '"'),
            ("'", "'"),
            ("`", "`"),
            ("“", "”"),
            ("‘", "’"),
            ("「", "」"),
            ("『", "』"),
        ]
        quote_base_values = [unquoted] if unquoted and unquoted != stripped else list(candidates)
        for value in quote_base_values:
            for left, right in quote_pairs:
                candidates.append(f"{left}{value}{right}")

        if unquoted and unquoted != stripped:
            candidates.append(unquoted)

        seen = set()
        unique = []
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                unique.append(candidate)
        return unique

    def normalized_with_index_map(self, text):
        chars = []
        index_map = []
        for idx, char in enumerate(text):
            if char.isspace():
                continue
            chars.append(self.normalize_alignment_char(char))
            index_map.append(idx)
        return "".join(chars), index_map

    def normalize_alignment_char(self, char):
        replacements = {
            "“": '"',
            "”": '"',
            "„": '"',
            "‟": '"',
            "＂": '"',
            "‘": "'",
            "’": "'",
            "‚": "'",
            "‛": "'",
            "＇": "'",
            "，": ",",
            "。": ".",
            "！": "!",
            "？": "?",
            "：": ":",
            "；": ";",
            "（": "(",
            "）": ")",
            "【": "[",
            "】": "]",
            "「": '"',
            "」": '"',
            "『": '"',
            "』": '"',
        }
        return replacements.get(char, char).lower()

    def containing_sentence_range(self, text, start, end):
        if not text:
            return 0, 0

        start = max(0, min(start, len(text)))
        end = max(start, min(end, len(text)))
        hard_breaks = "\n\r"

        left = start
        while left > 0:
            prev = text[left - 1]
            if prev in hard_breaks or self.is_sentence_boundary_at(text, left - 1):
                break
            left -= 1

        right = end
        while right < len(text):
            char = text[right]
            right += 1
            if char in hard_breaks or self.is_sentence_boundary_at(text, right - 1):
                break

        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1

        return left, right

    def is_sentence_boundary_at(self, text, index):
        if index < 0 or index >= len(text):
            return False

        char = text[index]
        if char in "。！？":
            return True

        if char not in ".!?":
            return False

        probe = index + 1
        while probe < len(text) and text[probe] in "\"'”’)]}":
            probe += 1

        if probe >= len(text):
            return True

        if not text[probe].isspace():
            return False

        while probe < len(text) and text[probe].isspace():
            probe += 1

        if probe >= len(text):
            return True

        next_char = text[probe]
        return next_char.isupper() or next_char.isdigit() or '\u4e00' <= next_char <= '\u9fff'

    def selection_is_whole_sentence(self, text, selection_start, selection_end, sentence_start, sentence_end):
        selected = text[selection_start:selection_end].strip()
        sentence = text[sentence_start:sentence_end].strip()
        return bool(selected) and selected == sentence

    def meaningful_selection(self, text, start, end):
        selected = text[start:end].strip()
        if not selected:
            return False

        if re.fullmatch(r'[\W_]+', selected, flags=re.UNICODE):
            return len(selected) >= 2 and any(char in "\"'`“”‘’()[]{}<>+-=*/\\|&%$#@" for char in selected)

        if re.search(r'[\u4e00-\u9fff]', selected):
            return True

        if re.search(r'[A-Za-z0-9_]', selected):
            left = start
            right = end
            while left < right and text[left].isspace():
                left += 1
            while right > left and text[right - 1].isspace():
                right -= 1

            before = text[left - 1] if left > 0 else ""
            after = text[right] if right < len(text) else ""
            if re.match(r'[A-Za-z0-9_]', before) or re.match(r'[A-Za-z0-9_]', after):
                return False
            return True

        return len(selected) >= 2

    def selection_context(self, text, start, end, radius=80):
        left = text[max(0, start - radius):start]
        right = text[end:min(len(text), end + radius)]
        return left, right

    def match_too_broad(self, selected_text, matched_text):
        selected_clean = re.sub(r'\s+', '', selected_text or "")
        matched_clean = re.sub(r'\s+', '', matched_text or "")
        if not selected_clean or not matched_clean:
            return False

        is_short_selection = len(selected_clean) <= 12
        if not is_short_selection:
            return False

        sentence_marks = "。！？.!?"
        looks_like_sentence = any(mark in matched_clean for mark in sentence_marks)
        too_long = len(matched_clean) > max(28, len(selected_clean) * 6)
        return looks_like_sentence and too_long

    def highlight_text_range(self, widget, start, end):
        cursor = widget.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)

        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = QTextCharFormat()
        selection.format.setBackground(QColor("#E8D7F1"))
        selection.format.setForeground(QColor("#1F2D3D"))
        widget.setExtraSelections([selection])

        view_cursor = widget.textCursor()
        view_cursor.setPosition(start)
        # Glide to the located text instead of jumping to it.
        with widget.smooth_scroller.glide():
            widget.setTextCursor(view_cursor)
            widget.ensureCursorVisible()

    def highlight_selected_counterpart(self):
        if not self.alignment_feature_enabled():
            self.reset_alignment_ui()
            return

        if not self.alignment_selection_source or not self.alignment_selection_range:
            self.btn_align_selection.hide()
            return

        selection_start, selection_end = self.alignment_selection_range
        origin_text = self.txt_origin.toPlainText()
        result_text = self.txt_result.toPlainText()

        if self.alignment_selection_source == "origin":
            source_text = origin_text
            target_text = result_text
            self.alignment_target_widget = self.txt_result
            self.txt_origin.setExtraSelections([])
        else:
            source_text = result_text
            target_text = origin_text
            self.alignment_target_widget = self.txt_origin
            self.txt_result.setExtraSelections([])

        selected_text = source_text[selection_start:selection_end].strip()
        if not selected_text or not target_text.strip():
            self.btn_align_selection.hide()
            return

        if not self.meaningful_selection(source_text, selection_start, selection_end):
            self.btn_align_selection.hide()
            return

        sentence_start, sentence_end = self.containing_sentence_range(source_text, selection_start, selection_end)
        selected_sentence = source_text[sentence_start:sentence_end].strip()
        selected_start_in_sentence = max(0, selection_start - sentence_start)
        selected_end_in_sentence = max(selected_start_in_sentence, selection_end - sentence_start)
        selection_is_sentence = self.selection_is_whole_sentence(
            source_text,
            selection_start,
            selection_end,
            sentence_start,
            sentence_end,
        )

        self.alignment_selected_text = selected_text
        self.alignment_selected_sentence = selected_sentence
        self.alignment_selection_is_sentence = selection_is_sentence

        direct_ranges = self.find_text_ranges(target_text, selected_text) if selection_is_sentence else []
        if selection_is_sentence and len(direct_ranges) == 1:
            direct_range = direct_ranges[0]
            self.highlight_text_range(self.alignment_target_widget, direct_range[0], direct_range[1])
            self.alignment_highlight_source = "result" if self.alignment_target_widget is self.txt_result else "origin"
            self.btn_align_selection.hide()
            return

        if not self.ensure_api_key():
            self.btn_align_selection.hide()
            return

        self.cancel_current_alignment()
        self.btn_align_selection.setEnabled(False)
        self.btn_align_selection.setText(tr("main.locate.busy"))
        self.btn_align_selection.adjustSize()

        left_context, right_context = self.selection_context(source_text, selection_start, selection_end)
        self.align_thread = AlignmentThread(
            source_text,
            target_text,
            selected_text,
            selected_sentence,
            selected_start_in_sentence,
            selected_end_in_sentence,
            left_context,
            right_context,
        )
        self.align_thread.finished.connect(self.on_alignment_finished)
        self.align_thread.start()

    def on_alignment_finished(self, success, match_data, error_msg):
        sender = self.sender()
        if sender is not None and sender is not self.align_thread:
            return

        self.btn_align_selection.setEnabled(True)

        if not success:
            log(f"[UI] alignment failed: {error_msg}")
            self.btn_align_selection.hide()
            return

        target_widget = getattr(self, "alignment_target_widget", None)
        if target_widget is None:
            self.btn_align_selection.hide()
            return

        if isinstance(match_data, dict):
            matched_text = match_data.get("text", "")
            target_sentence = match_data.get("target_sentence", "")
            left_context = match_data.get("target_left_context", "")
            right_context = match_data.get("target_right_context", "")
            occurrence_index = match_data.get("occurrence_index", 0)
        else:
            matched_text = str(match_data or "")
            target_sentence = ""
            left_context = ""
            right_context = ""
            occurrence_index = 0

        target_text = target_widget.toPlainText()
        search_text = target_text
        search_offset = 0

        sentence_range = self.find_text_range(target_text, target_sentence) if target_sentence else None
        if sentence_range:
            search_offset = sentence_range[0]
            search_text = target_text[sentence_range[0]:sentence_range[1]]
        elif target_sentence:
            log(f"[UI] alignment target sentence not found: {repr(target_sentence[:120])}")

        if not matched_text and self.alignment_selection_is_sentence and target_sentence:
            matched_text = target_sentence

        target_range = self.find_text_range(
            search_text,
            matched_text,
            left_context,
            right_context,
            occurrence_index,
        )
        if target_range:
            target_range = (target_range[0] + search_offset, target_range[1] + search_offset)

        if not target_range:
            log(f"[UI] alignment match not found in target text: {repr(matched_text[:120])}")
            self.btn_align_selection.hide()
            return

        if self.match_too_broad(self.alignment_selected_text, matched_text):
            log(f"[UI] alignment match rejected as too broad: {repr(matched_text[:120])}")
            self.btn_align_selection.hide()
            return

        self.highlight_text_range(target_widget, target_range[0], target_range[1])
        self.alignment_highlight_source = "result" if target_widget is self.txt_result else "origin"
        self.btn_align_selection.hide()

    # ---------- 回译 ----------
    def toggle_back_translation(self, checked):
        # Only this flag changes, so shortcut registration is left alone.
        candidate = replace(self.app_settings, back_translation=bool(checked))
        try:
            save_settings_atomic(self.settings_path, candidate)
        except (OSError, SettingsDataError):
            self.btn_back_translation.setChecked(self.app_settings.back_translation)
            show_warning(
                self,
                tr("warning.save_failed.title"),
                tr("warning.back_translation_save_failed.text"),
            )
            return
        self.app_settings = candidate
        self.sync_back_translation()

    def sync_back_translation(self):
        """Follow the saved setting: start for the shown result or put it away."""
        enabled = self.app_settings.back_translation
        self.btn_back_translation.setChecked(enabled)
        if not enabled:
            self.reset_back_translation()
        elif self.back_translation_panel.isHidden():
            self.start_back_translation()

    def back_translation_language(self) -> str:
        source_lang, _target_lang = self.current_dictionary_languages()
        if source_lang != "auto":
            return source_lang
        return detect_text_language(self.active_translation_text)

    def cancel_current_back_translation(self, wait_ms: int = 0):
        worker = self.back_thread
        self.back_thread = None
        if hasattr(self, "btn_stop_back_translation"):
            self.btn_stop_back_translation.hide()
        if worker and worker.isRunning():
            log("[UI] cancel_current_back_translation")
            # translation_threads keeps the worker alive until it stops.
            worker.request_stop()
            if wait_ms:
                worker.wait(wait_ms)

    def reset_back_translation(self):
        self.cancel_current_back_translation()
        self.back_render_timer.stop()
        self.back_translation_text = ""
        self.show_back_translation_message("")
        self.back_translation_panel.hide()

    def show_back_translation_message(self, message: str, *, error: bool = False):
        self.txt_back_translation.clear()
        self.txt_back_translation.setCurrentCharFormat(QTextCharFormat())
        if message:
            self.txt_back_translation.textCursor().insertText(
                message, pane_message_format(error)
            )

    def start_back_translation(self) -> bool:
        self.reset_back_translation()
        translation = self.full_translation.strip()
        if (
            not self.app_settings.back_translation
            or self.trans_thread is not None
            or not translation
            or self.active_translation_mode == "dictionary"
        ):
            return False

        language = self.back_translation_language()
        self.back_translation_language_code = language
        self.lbl_back_translation.setText(
            tr(
                "main.label.back_translation_into",
                language=dictionary_language_display(language),
            )
        )
        self.back_translation_panel.show()
        _source_lang, target_lang = self.current_dictionary_languages()
        if language == translation_target_language(self.active_translation_mode, target_lang):
            self.show_back_translation_message(
                tr("message.back_translation_same_language")
            )
            return False
        if not self.ensure_api_key(allow_prompt=False):
            self.show_back_translation_message(
                tr("message.back_translation_no_key"), error=True
            )
            return False

        worker = None
        try:
            worker = BackTranslationThread(
                translation,
                "简体中文" if language == "zh" else dictionary_language_label(language),
            )
            self.back_thread = worker
            self.translation_threads.add(worker)
            worker.chunk_received.connect(
                lambda chunk, active_worker=worker: self.on_back_translation_chunk(
                    active_worker,
                    chunk,
                )
            )
            worker.finished.connect(
                lambda success, error_msg, active_worker=worker:
                    self.on_back_translation_finished(
                        active_worker,
                        success,
                        error_msg,
                    )
            )
            worker.start()
        except Exception:
            if worker is not None:
                self.translation_threads.discard(worker)
            self.back_thread = None
            self.show_back_translation_message(
                tr("message.back_translation_error", error=tr("error.cannot_start")),
                error=True,
            )
            return False

        self.show_back_translation_message(tr("message.back_translation_running"))
        self.btn_stop_back_translation.show()
        return True

    def stop_back_translation(self):
        """Stop the back-translation and keep the part that arrived."""
        if self.back_thread is None:
            return
        self.cancel_current_back_translation()
        self.back_render_timer.stop()
        if not self.back_translation_text.strip():
            self.show_back_translation_message(tr("message.back_translation_stopped"))
            return
        self.render_back_translation()
        cursor = self.txt_back_translation.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(
            "\n\n" + tr("message.back_translation_stopped"), pane_message_format()
        )

    def on_back_translation_chunk(self, worker, chunk):
        if worker is not self.back_thread:
            return
        self.back_translation_text += chunk
        if not self.back_render_timer.isActive():
            self.back_render_timer.start()

    def on_back_translation_finished(self, worker, success, error_msg):
        self.translation_threads.discard(worker)
        if worker is not self.back_thread:
            return
        self.back_thread = None
        self.btn_stop_back_translation.hide()
        self.back_render_timer.stop()

        received = bool(self.back_translation_text.strip())
        if received:
            self.render_back_translation()
        if success:
            if not received:
                self.show_back_translation_message(tr("message.back_translation_empty"))
            return

        error = tr("message.back_translation_error", error=error_msg)
        if not received:
            self.show_back_translation_message(error, error=True)
            return
        cursor = self.txt_back_translation.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(f"\n\n{error}", pane_message_format(error=True))

    def render_back_translation(self):
        """Render the back-translation without moving the reader's place in it."""
        scroll_bar = self.txt_back_translation.verticalScrollBar()
        position = scroll_bar.value()
        self.render_markdown_text(
            self.txt_back_translation, self.back_translation_text, "back",
        )
        scroll_bar.setValue(position)

    # ---------- 模式切换 ----------
    def apply_manual_mode_ui(self, reset_content=False):
        self.source_mode = "manual"
        self.reset_alignment_ui()

        self.btn_content_mode.show()
        self.btn_edit_origin.hide()
        self.btn_clear_origin.show()
        self.refresh_result_hint()
        self.btn_ocr_clipboard.show()
        self.btn_ocr_clipboard.setEnabled(True)
        self.btn_translate.show()
        self.update_content_mode_button_text()
        self.set_dictionary_language_controls_visible(True)
        self.txt_origin.setReadOnly(False)
        self.txt_origin.setMaximumHeight(220)

        self.refresh_stop_button()

        if self.content_mode_override == "dictionary":
            self.show_heading_labels("dictionary")
        else:
            self.show_heading_labels(self.selected_translation_mode())
        self.refresh_origin_placeholder()

        if reset_content:
            self.original_paragraphs = []
            self.full_translation = ""
            self.reset_origin_editor()
            self.setup_result_format()
            self.btn_translate.setEnabled(True)
            self.btn_translate.setText(tr("main.translate"))
            self.btn_copy.setText(tr("main.copy"))
            self.btn_copy.setEnabled(False)

    def apply_snipdo_mode_ui(self):
        self.source_mode = "snipdo"
        self.reset_alignment_ui()

        self.btn_content_mode.show()
        self.update_content_mode_button_text()
        self.btn_translate.hide()
        self.btn_clear_origin.hide()
        self.btn_edit_origin.show()
        self.refresh_result_hint()
        self.refresh_stop_button()
        self.btn_ocr_clipboard.hide()
        self.set_dictionary_language_controls_visible(True)
        self.txt_origin.setReadOnly(True)
        self.txt_origin.setPlaceholderText("")

        total_text = "\n".join(self.original_paragraphs).strip()
        self.btn_edit_origin.setEnabled(bool(total_text))
        effective_mode = self.resolve_effective_mode(total_text)
        dict_mode = effective_mode == "dictionary"

        self.show_heading_labels("dictionary" if dict_mode else effective_mode)
        self.fit_origin_height()

    def fit_origin_height(self):
        """A looked-up word gets a box its own size, leaving the room to the entry."""
        if self.source_mode != "snipdo":
            return
        if self.heading_mode != "dictionary":
            self.txt_origin.setMaximumHeight(180)
            return
        document = self.txt_origin.document()
        document.setTextWidth(self.txt_origin.viewport().width())
        margins = self.txt_origin.contentsMargins()
        chrome = (
            self.txt_origin.height() - self.txt_origin.viewport().height()
            if self.txt_origin.isVisible()
            else margins.top() + margins.bottom() + 8
        )
        self.txt_origin.setMaximumHeight(
            min(180, math.ceil(document.size().height()) + chrome)
        )

    def refresh_origin_placeholder(self):
        if self.source_mode != "manual":
            self.txt_origin.setPlaceholderText("")
        elif self.content_mode_override == "dictionary":
            self.txt_origin.setPlaceholderText(tr("main.placeholder.dictionary"))
        else:
            self.txt_origin.setPlaceholderText(tr("main.placeholder.translate"))

    def retranslate_ui(self, previous_language: str):
        """Re-label what is on screen after the UI language changes.

        Text already in the result panes stays as it was written.
        """
        previous = messages(previous_language)

        def follow(widget, keys):
            # Buttons whose text tracks their state keep that state.
            for key in keys:
                if widget.text() == previous[key]:
                    widget.setText(tr(key))
                    return

        if hasattr(self, "tray_menu"):
            self.toggle_translation_action.setText(tr("tray.disable"))
            self.settings_action.setText(tr("tray.settings"))
            self.ocr_action.setText(tr("tray.ocr_clipboard"))
            self.check_updates_action.setText(tr("tray.check_updates"))
            self.about_action.setText(tr("tray.about", version=APP_VERSION))
            self.quit_action.setText(tr("tray.quit"))

        self.refresh_update_indicator()
        self.update_content_mode_button_text()
        self.lbl_dictionary_lang.setText(tr("main.language"))
        self.lbl_dictionary_lang.setToolTip(tr("main.language.tooltip"))
        self.set_dictionary_language_controls_visible(
            not self.cbo_dictionary_source.isHidden()
        )
        self.cbo_dictionary_source.setToolTip(tr("main.language.source_tooltip"))
        self.cbo_dictionary_target.setToolTip(tr("main.language.target_tooltip"))
        # Languages keep their own names; only "Auto" and "Default" change.
        for combo in (self.cbo_dictionary_source, self.cbo_dictionary_target):
            for index in range(combo.count()):
                combo.setItemText(index, native_language_name(combo.itemData(index)))
        self.show_heading_labels(self.heading_mode)
        self.refresh_origin_placeholder()

        self.btn_back_translation.setText(tr("main.back_translation"))
        self.btn_back_translation.setToolTip(tr("main.back_translation.tooltip"))
        if self.back_translation_panel.isHidden() or not self.back_translation_language_code:
            self.lbl_back_translation.setText(tr("main.label.back_translation"))
        else:
            self.lbl_back_translation.setText(
                tr(
                    "main.label.back_translation_into",
                    language=dictionary_language_display(
                        self.back_translation_language_code
                    ),
                )
            )
        self.btn_stop_back_translation.setText(tr("main.stop_back_translation"))
        self.btn_stop_back_translation.setToolTip(
            tr("main.stop_back_translation.tooltip")
        )

        self.btn_edit_origin.setText(tr("main.edit"))
        self.btn_edit_origin.setToolTip(tr("main.edit.tooltip"))
        self.btn_clear_origin.setText(tr("main.clear"))
        self.btn_ocr_clipboard.setText(tr("main.ocr"))
        self.btn_ocr_clipboard.setToolTip(tr("main.ocr.tooltip"))
        self.btn_history.setText(tr("main.history"))
        self.btn_stop.setText(tr("main.stop"))
        self.btn_stop.setToolTip(tr("main.stop.tooltip"))
        self.btn_hide.setText(tr("main.hide"))
        busy_keys = ("main.translating", "main.looking_up", "main.ocr.busy")
        follow(self.btn_translate, ("main.translate", *busy_keys))
        self.match_translate_and_stop_widths()
        follow(self.btn_copy, ("main.copy", "main.copied", *busy_keys))
        follow(
            self.btn_align_selection,
            ("main.locate.translation", "main.locate.original", "main.locate.busy"),
        )
        self.btn_align_selection.adjustSize()

        self.refresh_shortcut_status()
        self.refresh_api_status()

    # ---------- 原文显示 ----------
    def populate_original_text(self):
        self.txt_origin.clear()

        total_text = "\n".join(self.original_paragraphs).strip()
        if self.current_request_is_structured and total_text:
            self.render_markdown_text(self.txt_origin, total_text, "origin")
            return

        cursor = self.txt_origin.textCursor()

        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(13 if self.source_mode == "snipdo" else 14)

        block_fmt = QTextBlockFormat()
        block_fmt.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        block_fmt.setBottomMargin(8)

        char_fmt = QTextCharFormat()
        char_fmt.setFont(font)
        char_fmt.setForeground(QColor("#606266" if self.source_mode == "snipdo" else "#303133"))

        cursor.beginEditBlock()
        for idx, para in enumerate(self.original_paragraphs):
            cursor.insertText(para, char_fmt)
            cursor.setBlockFormat(block_fmt)
            if idx != len(self.original_paragraphs) - 1:
                cursor.insertBlock()
        cursor.endEditBlock()

        self.txt_origin.moveCursor(QTextCursor.MoveOperation.Start)
        self.fit_origin_height()

    def ensure_large_window_size(self):
        if not self.isMaximized():
            self.resize(NORMAL_WINDOW_WIDTH, NORMAL_WINDOW_HEIGHT)

    # ---------- 请求入口 ----------
    def handle_new_request(self, raw_text, *, allow_key_prompt: bool = True):
        try:
            log(f"[UI] handle_new_request received: {repr(raw_text[:300])}")
            if not isinstance(raw_text, str):
                return False

            self.cancel_current_ocr()
            self.cancel_current_translation()

            self.original_paragraphs = normalize_input_text(raw_text)
            log(f"[UI] normalized paragraphs count={len(self.original_paragraphs)}")

            if not self.original_paragraphs:
                log("[UI] no valid paragraphs after normalize_input_text")
                return False

            total_text = "\n".join(self.original_paragraphs).strip()
            self.pending_snipdo_text = total_text
            self.current_request_is_structured = is_structured_text(total_text)

            effective_mode = self.resolve_effective_mode(total_text)

            self.full_translation = ""
            self.btn_copy.setText(tr("main.looking_up" if effective_mode == "dictionary" else "main.translating"))
            self.btn_copy.setEnabled(False)

            log(f"[UI] effective_mode={effective_mode}, total_text={repr(total_text[:300])}")

            self.apply_snipdo_mode_ui()
            self.ensure_large_window_size()
            self.populate_original_text()
            self.setup_result_format()

            self.force_show_window()

            return self.start_translation(
                total_text,
                effective_mode,
                allow_key_prompt=allow_key_prompt,
            )
        except Exception:
            record_event(AppEvent.REQUEST_REJECTED)
            return False

    def handle_app_request(
        self,
        request: AppRequest,
        *,
        allow_key_prompt: bool = True,
    ) -> ReceiveResult:
        """Synchronously decide whether this process has taken ownership."""
        accepted = False
        reason = RejectionReason.INVALID_REQUEST
        try:
            if not isinstance(request, AppRequest):
                return ReceiveResult.reject(reason)
            if request.action == "show":
                self.show_manual_window()
                accepted = True
            elif request.action == "translate_text":
                text = request.payload.get("text")
                if not isinstance(text, str) or not text.strip():
                    return ReceiveResult.reject(reason)
                if not normalize_input_text(text):
                    reason = RejectionReason.NOT_OWNED
                    return ReceiveResult.reject(reason)
                if client is None and not allow_key_prompt:
                    reason = RejectionReason.MISSING_KEY
                    return ReceiveResult.reject(reason)
                accepted = self.handle_new_request(
                    text,
                    allow_key_prompt=allow_key_prompt,
                )
                reason = (
                    RejectionReason.MISSING_KEY
                    if client is None
                    else RejectionReason.STARTUP_FAILED
                )
            elif request.action == "ocr_image":
                file_path = request.payload.get("path")
                if not isinstance(file_path, str) or not file_path:
                    return ReceiveResult.reject(reason)
                if self.ocr_thread and self.ocr_thread.isRunning():
                    reason = RejectionReason.BUSY
                    return ReceiveResult.reject(reason)
                if not api_provider_for_window(self).supports_vision:
                    return self.start_image_file_ocr(
                        file_path,
                        allow_key_prompt=allow_key_prompt,
                    )
                if client is None and not allow_key_prompt:
                    reason = RejectionReason.MISSING_KEY
                    return ReceiveResult.reject(reason)
                result = self.start_image_file_ocr(
                    file_path,
                    allow_key_prompt=allow_key_prompt,
                )
                accepted = result.accepted
                return result
            return (
                ReceiveResult.accept()
                if accepted
                else ReceiveResult.reject(reason)
            )
        except Exception:
            return ReceiveResult.reject(RejectionReason.HANDLER_FAILED)
        finally:
            record_event(
                AppEvent.REQUEST_ACCEPTED if accepted else AppEvent.REQUEST_REJECTED
            )

    def start_manual_translation(self):
        if self.source_mode != "manual":
            return

        text_to_translate = self.txt_origin.toPlainText().strip()
        if not text_to_translate:
            return

        effective_mode = self.resolve_effective_mode(text_to_translate)
        dictionary_source_lang, dictionary_target_lang = self.current_dictionary_languages()
        self.current_request_is_structured = is_structured_text(text_to_translate)

        self.cancel_current_translation()

        self.original_paragraphs = [p.strip() for p in re.split(r'\n+', normalize_newlines(text_to_translate)) if p.strip()]
        self.full_translation = ""
        self.setup_result_format()
        self.ensure_large_window_size()

        self.set_dictionary_language_controls_visible(True)
        self.show_heading_labels(effective_mode)

        self.btn_translate.setEnabled(False)
        self.btn_translate.setText(tr("main.looking_up" if effective_mode == "dictionary" else "main.translating"))
        self.btn_copy.setEnabled(False)
        self.btn_copy.setText(tr("main.copy"))

        self.start_translation(
            text_to_translate,
            effective_mode,
            dictionary_source_lang,
            dictionary_target_lang,
        )

    def start_translation(
        self,
        text: str,
        mode: str,
        dictionary_source_lang: str = None,
        dictionary_target_lang: str = None,
        *,
        allow_key_prompt: bool = True,
    ):
        log(f"[UI] start_translation, mode={mode}, text={repr(text[:300])}")
        self.active_translation_text = text
        self.active_translation_mode = mode

        if not self.ensure_api_key(allow_prompt=allow_key_prompt):
            self.show_result_notice(tr("message.translation_no_key"), retry=True)

            if self.source_mode == "manual":
                self.btn_translate.setEnabled(True)
                self.btn_translate.setText(tr("main.translate"))

            self.btn_copy.setText(tr("main.copy"))
            self.btn_copy.setEnabled(False)
            self.force_show_window()
            return False

        if dictionary_source_lang is None or dictionary_target_lang is None:
            dictionary_source_lang, dictionary_target_lang = self.current_dictionary_languages()

        worker = None
        try:
            worker = TranslationThread(
                text,
                mode,
                dictionary_source_lang,
                dictionary_target_lang,
            )
            self.trans_thread = worker
            self.translation_threads.add(worker)
            worker.chunk_received.connect(
                lambda chunk, active_worker=worker: self.on_translation_worker_chunk(
                    active_worker,
                    chunk,
                )
            )
            worker.finished.connect(
                lambda success, error_msg, active_worker=worker:
                    self.on_translation_worker_finished(
                        active_worker,
                        success,
                        error_msg,
                    )
            )
            worker.start()
        except Exception:
            if worker is not None:
                self.translation_threads.discard(worker)
            if self.trans_thread is worker:
                self.trans_thread = None
            self.force_show_window()
            self.set_result_message(
                tr("message.translation_error", error=tr("error.cannot_start")),
                retry=True,
            )
            record_event(AppEvent.TRANSLATION_FAILED)
            return False

        record_event(AppEvent.TRANSLATION_STARTED)
        self.result_busy.start(
            tr("main.looking_up" if mode == "dictionary" else "main.translating")
        )
        self.refresh_stop_button()
        if not self.connection_refresh_timer.isActive():
            self.connection_refresh_timer.start()
        if prepare_latex_math_for_document(text)[1]:
            # Load the formula renderer while the response streams in.
            warm_up_math_rendering()
        return True

    # ---------- 结果输出 ----------
    def on_translation_worker_chunk(self, worker, chunk):
        if worker is self.trans_thread:
            self.append_translation_chunk(chunk)

    def on_translation_worker_finished(self, worker, success, error_msg):
        self.translation_threads.discard(worker)
        if worker is not self.trans_thread:
            return
        self.trans_thread = None
        self.on_translation_finished(success, error_msg)

    def append_translation_chunk(self, chunk):
        self.full_translation += chunk
        if self.result_busy.is_active():
            self.result_busy.stop()
        if self.live_render_timer.isActive():
            return
        delay = self._live_render_due - time.monotonic()
        if delay <= 0:
            self.render_live_translation()
        else:
            # A trailing render picks up every chunk that arrives meanwhile.
            self.live_render_timer.start(int(delay * 1000) + 1)

    def on_live_render_timer(self):
        if self.trans_thread is not None:
            self.render_live_translation()

    def flush_live_translation(self):
        if self.live_render_timer.isActive():
            self.render_live_translation()

    def render_result_in_place(self, *, preview: bool) -> None:
        """Re-render the response without moving the reader's scroll position."""
        scroll_bar = self.txt_result.verticalScrollBar()
        position = scroll_bar.value()
        self.render_markdown_text(
            self.txt_result, self.full_translation, "result", preview=preview,
        )
        scroll_bar.setValue(position)

    def render_live_translation(self):
        """Render the partial response as Markdown while it streams."""
        self.live_render_timer.stop()
        started = time.monotonic()
        self.render_result_in_place(preview=True)
        finished = time.monotonic()
        # Long responses take longer to re-render; keep the UI thread mostly free.
        self._live_render_due = finished + max(
            LIVE_RENDER_INTERVAL_SECONDS, 4 * (finished - started),
        )

    def on_translation_finished(self, success, error_msg):
        log(f"[UI] on_translation_finished success={success}, error={error_msg}")
        self.result_busy.stop()
        if success:
            self.live_render_timer.stop()
        else:
            self.flush_live_translation()
        if not success:
            if error_msg == CANCELLED_ERROR:
                record_event(AppEvent.TRANSLATION_CANCELLED)
            else:
                record_event(AppEvent.TRANSLATION_FAILED)
                self.show_result_notice(
                    tr("message.translation_error", error=error_msg), retry=True
                )
        elif self.full_translation.strip():
            record_event(AppEvent.TRANSLATION_COMPLETED)
            self.render_result_in_place(preview=False)
            self.add_history_entry(
                self.active_translation_text,
                self.full_translation,
                self.active_translation_mode,
            )
            self.notify_translation_completed()
            self.start_back_translation()
        else:
            record_event(AppEvent.TRANSLATION_FAILED)

        if self.source_mode == "manual":
            self.btn_translate.setEnabled(True)
            self.btn_translate.setText(tr("main.translate"))

        self.btn_copy.setText(tr("main.copy"))
        self.btn_copy.setEnabled(bool(self.full_translation.strip()))
        self.refresh_stop_button()

        if self.source_mode == "snipdo":
            self.ensure_large_window_size()

    def refresh_stop_button(self):
        """Stop takes the Translate button's place while a translation runs."""
        if not hasattr(self, "btn_stop"):
            return
        running = self.trans_thread is not None
        self.btn_stop.setVisible(running)
        self.btn_translate.setVisible(self.source_mode == "manual" and not running)

    def stop_translation(self):
        """Stop generating and keep the part of the translation that arrived."""
        worker = self.trans_thread
        if worker is None:
            return
        log("[UI] stop_translation")
        # Detach at once: a worker still waiting for the API only sees the
        # request at its next chunk, and its late signals are ignored.
        self.trans_thread = None
        worker.request_stop()
        self.on_translation_finished(False, CANCELLED_ERROR)
        self.show_result_notice(tr("message.stopped"), error=False)

    # ---------- 剪贴板 ----------
    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        if self.full_translation:
            clipboard.setText(self.full_translation.strip())
            self.btn_copy.setText(tr("main.copied"))
            QTimer.singleShot(900, lambda: self.btn_copy.setText(tr("main.copy")))
            log("[Clipboard] copied translation")

    # ---------- 关闭行为 ----------
    def changeEvent(self, event):
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.ActivationChange
            and self.isActiveWindow()
        ):
            self.clear_translation_unread()

    def closeEvent(self, event):
        if self.force_quit:
            log("[UI] closeEvent force quit")
            self.cancel_current_translation()
            self.shutdown_translation_shortcut()
            super().closeEvent(event)
        else:
            log("[UI] closeEvent hide to tray")
            event.ignore()
            self.hide()


def configure_runtime(paths: AppPaths) -> None:
    global APP_PATHS, EVENT_LOG

    APP_PATHS = paths
    ensure_app_directories(paths)
    EVENT_LOG = configure_app_logging(paths)
    record_event(AppEvent.APP_STARTING)


def close_runtime_log() -> None:
    global EVENT_LOG

    if EVENT_LOG is None:
        return
    try:
        EVENT_LOG.close()
    except Exception:
        pass
    finally:
        EVENT_LOG = None


def run_offline_self_test(paths: AppPaths) -> int:
    """Exercise frozen imports and local codecs without credentials or network."""
    try:
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError("unsupported platform")
        for icon_path in (paths.icon_path, paths.disabled_icon_path):
            if not icon_path.is_file() or QImage(str(icon_path)).isNull():
                raise RuntimeError("bundled icon is unavailable")
        # Every bundled changelog must describe the version being shipped.
        for language, name in CHANGELOG_FILES.items():
            if not (paths.bundle_dir / name).is_file() or not load_changelog(
                paths.bundle_dir, language
            ).startswith(f"## {APP_VERSION} "):
                raise RuntimeError("bundled changelog is unavailable or outdated")

        request = AppRequest("show", request_id="offline-self-test")
        frame = encode_json_frame(request.to_dict())
        if len(frame) <= 4:
            raise RuntimeError("framing failed")
        restored = validate_request_dict(decode_json_payload(frame[4:]))
        if restored != request:
            raise RuntimeError("protocol round trip failed")

        prepared_math, math_fragments = prepare_latex_math_for_document(
            r"\[\frac{-b \pm \sqrt{b^2-4ac}}{2a}\]"
        )
        if len(math_fragments) != 1 or math_fragments[0].placeholder not in prepared_math:
            raise RuntimeError("formula parsing failed")
        rendered_formula = render_latex_fragment_png(math_fragments[0])
        rendered_image = QImage.fromData(rendered_formula.png, "PNG")
        if rendered_image.isNull() or rendered_image.width() <= 1:
            raise RuntimeError("formula rendering failed")

        # A successful fixed-event write also verifies the per-user data/log
        # directory without exposing its path in output.
        record_event(AppEvent.SELF_TEST_PASSED)
        if EVENT_LOG is not None:
            EVENT_LOG.flush()
        return 0
    except Exception:
        record_event(AppEvent.SELF_TEST_FAILED)
        return 1


def settle_source_file(prepared: PreparedRequest, accepted: bool) -> None:
    if prepared.delete_path is None:
        return
    if not accepted:
        record_event(AppEvent.SOURCE_DELETE_SKIPPED)
        return
    try:
        if delete_acknowledged_source(prepared, accepted=True):
            record_event(AppEvent.SOURCE_DELETE_COMPLETED)
    except OSError:
        record_event(AppEvent.SOURCE_DELETE_FAILED)


def initialize_history(paths: AppPaths) -> None:
    try:
        if migrate_legacy_history(paths):
            record_event(AppEvent.HISTORY_MIGRATED)
    except (HistoryDataError, OSError):
        record_event(AppEvent.HISTORY_UNAVAILABLE)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        command = parse_cli(arguments)
    except CliError:
        return 2

    try:
        paths = resolve_app_paths(__file__)
        configure_runtime(paths)
    except Exception:
        close_runtime_log()
        return 1

    if command.action == "self_test":
        try:
            if command.value != "offline":
                record_event(AppEvent.SELF_TEST_FAILED)
                return 2
            return run_offline_self_test(paths)
        finally:
            close_runtime_log()

    try:
        prepared = prepare_request(command)
    except (CliError, OSError, UnicodeError):
        record_event(AppEvent.REQUEST_REJECTED)
        close_runtime_log()
        return 2

    try:
        app = QApplication.instance() or QApplication([sys.argv[0]])
        app.setQuitOnLastWindowClosed(False)
        bridge = QtIpcRequestBridge()
        server = SingleInstanceServer(APP_ID, bridge.receive)
        is_primary = server.start()
    except Exception:
        record_event(AppEvent.IPC_UNAVAILABLE)
        settle_source_file(prepared, accepted=False)
        close_runtime_log()
        return 1

    if not is_primary:
        record_event(AppEvent.INSTANCE_SECONDARY)
        if command.action == "startup":
            # Sign-in must not raise or clear an already running window.
            close_runtime_log()
            return 0
        try:
            ack = send_request(APP_ID, prepared.request, timeout=5.0)
        except IpcError:
            record_event(AppEvent.IPC_UNAVAILABLE)
            # The prior primary may have died after creating its mutex but
            # before serving the pipe.  Re-run the same atomic election once.
            try:
                is_primary = server.start()
            except (IpcError, OSError, RuntimeError):
                is_primary = False
            if not is_primary:
                settle_source_file(prepared, accepted=False)
                close_runtime_log()
                return 4
        else:
            record_event(
                AppEvent.IPC_FORWARD_ACCEPTED
                if ack.accepted
                else AppEvent.IPC_FORWARD_REJECTED
            )
            settle_source_file(prepared, accepted=ack.accepted)
            close_runtime_log()
            return 0 if ack.accepted else 3

    record_event(AppEvent.INSTANCE_PRIMARY)
    record_event(AppEvent.IPC_LISTENING)

    try:
        initialize_history(paths)
        try:
            startup_settings = load_settings(
                paths.data_dir / SETTINGS_FILE_NAME
            )
        except (SettingsDataError, OSError):
            startup_settings = DEFAULT_SETTINGS
        store = initialize_credentials(
            paths,
            startup_settings.api_provider,
            **({"config": startup_settings.api_configs[startup_settings.api_provider]}
               if startup_settings.api_provider in startup_settings.api_configs else {}),
        )
        window = TranslationWindow(
            paths,
            store,
            initial_settings=startup_settings,
        )
        app.setApplicationVersion(APP_VERSION)
        app.aboutToQuit.connect(window.shutdown_translation_shortcut)
        app.aboutToQuit.connect(window.update_notifications.stop)
        if server.failed or not server.running:
            raise RuntimeError("IPC server stopped during startup")
        bridge.bind(window)
        record_event(AppEvent.APP_READY)
    except Exception:
        bridge.fail(RejectionReason.STARTUP_FAILED)
        record_event(AppEvent.REQUEST_REJECTED)
        settle_source_file(prepared, accepted=False)
        try:
            server.stop()
        except IpcError:
            record_event(AppEvent.IPC_UNAVAILABLE)
        close_runtime_log()
        return 1

    if command.action != "startup":
        initial_result = window.handle_app_request(
            prepared.request,
            allow_key_prompt=True,
        )
        settle_source_file(prepared, accepted=initial_result.accepted)
    window.update_notifications.start()
    # Acknowledge only after the primary instance has initialized successfully
    # and reached the event loop, so a failed launch can still be rolled back.
    def finish_update_startup():
        try:
            acknowledge_update_startup(APP_VERSION)
            update_error = consume_update_error()
        except (OSError, ValueError, RuntimeError):
            return
        if update_error:
            window.update_notifications._show_status(update_error)

    QTimer.singleShot(0, finish_update_startup)

    exit_code = 1
    try:
        exit_code = app.exec()
    except Exception:
        exit_code = 1
    finally:
        bridge.fail(RejectionReason.SHUTTING_DOWN)
        try:
            server.stop()
        except IpcError:
            record_event(AppEvent.IPC_UNAVAILABLE)
        record_event(AppEvent.APP_STOPPED)
        close_runtime_log()
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
