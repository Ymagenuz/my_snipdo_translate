import sys
import os
import re
import json
import warnings
import time
import ctypes
import base64
import mimetypes
import uuid
import threading
from ctypes import wintypes
from dataclasses import dataclass, replace
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote

from httpx import Limits
from openai import DefaultHttpxClient, OpenAI

from api_providers import (
    DEFAULT_API_PROVIDER,
    ApiProviderSpec,
    api_provider_options,
    chat_completion_options,
    get_api_provider,
)

from app_cli import (
    AppRequest,
    CliError,
    PreparedRequest,
    delete_acknowledged_source,
    parse_cli,
    prepare_request,
)
from app_logging import AppEvent, PrivacyEventLogger, configure_app_logging
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
)

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTextEdit,
    QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QSystemTrayIcon, QMenu, QInputDialog, QLineEdit,
    QComboBox, QDialog, QCheckBox, QFormLayout,
    QDialogButtonBox, QMessageBox
)
from PyQt6.QtGui import (
    QColor, QScreen, QTextCursor, QTextCharFormat,
    QTextBlockFormat, QTextFormat, QFont, QAction, QIcon, QImage,
    QKeySequence, QPainter, QPen
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QThread, QObject, QTimer, QByteArray, QBuffer,
    QIODevice, QMimeData, QKeyCombination, QAbstractNativeEventFilter,
    QEvent, QRectF
)

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
MODEL_NAME = get_api_provider(DEFAULT_API_PROVIDER).model
REQUEST_TIMEOUT_SECONDS = 15.0
HTTP_KEEPALIVE_SECONDS = 60.0
MAX_HISTORY_ITEMS = 50
APP_ID = "SnipDoTranslate"
APP_DISPLAY_NAME = "SnipDo Translate"
IPC_UI_TIMEOUT_SECONDS = 4.0
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


def activate_api_runtime(api_provider: str, api_client: object | None) -> None:
    global api_runtime, client

    api_runtime = ApiRuntime(get_api_provider(api_provider), api_client)
    # Keep the original module global as a compatibility alias for existing
    # integrations. Runtime calls use the immutable snapshot above.
    client = api_client


def create_api_client(
    api_key: str,
    api_provider: str = DEFAULT_API_PROVIDER,
):
    api_key = (api_key or "").strip()
    if is_placeholder_api_key(api_key):
        return None

    try:
        provider = get_api_provider(api_provider)
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
        return OpenAI(
            api_key=api_key,
            base_url=provider.base_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
            http_client=http_client,
        )
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
) -> bool:
    candidate = create_api_client(api_key, api_provider)
    activate_api_runtime(api_provider, candidate)
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
        return get_api_provider(provider_id)
    except ValueError:
        return get_api_provider(DEFAULT_API_PROVIDER)


def initialize_credentials(
    paths: AppPaths,
    api_provider: str = DEFAULT_API_PROVIDER,
) -> WindowsCredentialStore | None:
    """Resolve a key without making a network request or writing plaintext."""
    global CREDENTIAL_STORE

    try:
        provider = get_api_provider(api_provider)
    except ValueError:
        provider = get_api_provider(DEFAULT_API_PROVIDER)
        api_provider = provider.provider_id
    activate_api_runtime(api_provider, None)

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
VK_CONTROL = 0x11
VK_C = 0x43
KEYEVENTF_KEYUP = 0x0002

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
        raise RuntimeError("剪贴板中没有图片")

    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("无法读取剪贴板图片")

    try:
        if not image.save(buffer, "PNG"):
            raise RuntimeError("无法把剪贴板图片转换为 PNG")
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


def send_ctrl_c():
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


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
        return "ENGLISH TRANSLATION"
    if mode == "en2zh":
        return "CHINESE TRANSLATION"
    return "AUTO TRANSLATION"


def translation_mode_for_target_language(target_lang: str) -> str:
    if target_lang == "en":
        return "zh2en"
    if target_lang == "zh":
        return "en2zh"
    return "auto"


def translation_label_for_target_language(mode: str, target_lang: str) -> str:
    if target_lang == "default":
        return target_label_for_mode(mode)
    if target_lang == "zh":
        return "CHINESE TRANSLATION"
    if target_lang == "en":
        return "ENGLISH TRANSLATION"
    return f"{dictionary_language_label(target_lang, target=True).upper()} TRANSLATION"


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
    options = DICTIONARY_TARGET_LANGUAGES if target else DICTIONARY_SOURCE_LANGUAGES
    return next((label for value, label in options if value == code), "Auto")


# ================= 1. 统一后台翻译/查词线程 =================
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
        try:
            runtime = self.runtime
            if self._stop_requested:
                self.finished.emit(False, "已取消")
                return
            if runtime.client is None:
                raise RuntimeError(
                    f"未设置 {runtime.provider.display_name} API Key"
                )

            prompt = self.build_prompt()
            format_instruction = markdown_format_instruction(self.text)
            if format_instruction and self.mode != "dictionary":
                prompt = f"{format_instruction}\n\n{prompt}"
            log(f"[TranslateThread] start, mode={self.mode}, text={repr(self.text[:200])}")

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

            for chunk in response:
                if self._stop_requested:
                    log("[TranslateThread] cancelled")
                    self.finished.emit(False, "已取消")
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

            remaining_content = stream_restorer.finish()
            if remaining_content:
                self.chunk_received.emit(remaining_content)

            log("[TranslateThread] finished success")
            self.finished.emit(True, "")
        except Exception as e:
            log(f"[TranslateThread] error: {e}")
            self.finished.emit(False, str(e))


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
                self.finished.emit(False, {"text": ""}, "已取消")
                return
            if runtime.client is None:
                raise RuntimeError(
                    f"未设置 {runtime.provider.display_name} API Key"
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
                self.finished.emit(False, {"text": ""}, "已取消")
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
                self.finished.emit(False, "", "已取消")
                return
            if runtime.client is None:
                raise RuntimeError(
                    f"未设置 {runtime.provider.display_name} API Key"
                )
            if not runtime.provider.supports_vision:
                raise RuntimeError(
                    f"{runtime.provider.display_name} 不支持图片 OCR"
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
                self.finished.emit(False, "", "已取消")
                return

            content = response.choices[0].message.content or ""
            text = normalize_newlines(content).strip()
            if text.strip().upper() == "NO_TEXT_FOUND":
                raise RuntimeError("未识别到文字")

            log(f"[OcrThread] finished success, text={repr(text[:200])}")
            self.finished.emit(True, text, "")
        except Exception as e:
            log(f"[OcrThread] error: {e}")
            self.finished.emit(False, "", str(e))


# ================= 3. 统一文本框：支持 Ctrl+Enter =================
class NoWheelComboBox(QComboBox):
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
                selection-background-color: #B3D8FF;
                selection-color: #303133;
            }
            QScrollBar:vertical {
                border: none;
                background: #F0F0F0;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #C0C4CC;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #909399;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

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


def shortcuts_conflict(
    first: ShortcutBinding,
    second: ShortcutBinding,
) -> bool:
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

    def __init__(self, binding: ShortcutBinding, parent=None):
        super().__init__(parent)
        self._binding = binding
        self._capturing = False
        self.setMinimumWidth(180)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击后按下新的全局快捷键；Esc 取消")
        self.setStyleSheet("""
            QPushButton {
                background-color: #F5F7FA;
                color: #606266;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 7px 12px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover, QPushButton:focus {
                border-color: #8E44AD;
                color: #8E44AD;
            }
        """)
        self.clicked.connect(self.begin_capture)
        self._refresh_text()

    @property
    def binding(self) -> ShortcutBinding:
        return self._binding

    def set_binding(self, binding: ShortcutBinding) -> None:
        self._binding = binding
        if not self._capturing:
            self._refresh_text()

    def _refresh_text(self) -> None:
        self.setText(self._binding.display)

    def begin_capture(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self.setText("请按下快捷键…")
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
        if not self._capturing:
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_Escape:
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
            self.capture_error.emit("无法识别该按键，请换一个组合键。")
            event.accept()
            return

        try:
            binding = keyboard_shortcut(virtual_key, modifiers, display)
        except (SettingsDataError, TypeError, ValueError):
            self.capture_error.emit("普通按键需要搭配 Ctrl、Alt、Shift 或 Win；F1–F24 可单独使用。")
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
            self.capture_error.emit("鼠标快捷键支持 XButton1、XButton2 和中键；Esc 取消。")
            event.accept()
            return

        self._binding = mouse_shortcut(button_name)
        self._finish_capture()
        self._refresh_text()
        event.accept()


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: AppSettings,
        *,
        api_key_configured: bool,
        environment_key_active: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._initial_api_provider = settings.api_provider
        self._api_key_configured = api_key_configured
        self._environment_key_active = environment_key_active
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(470)
        self.setStyleSheet("""
            QDialog {
                background-color: #F5F7FA;
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
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #8E44AD;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title = QLabel("翻译工具设置")
        title.setStyleSheet("color: #303133; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        description = QLabel(
            "启用状态控制全局划词翻译；主窗口和 SnipDo 调用仍可继续使用。"
            "显示窗口快捷键始终有效。"
            "鼠标快捷键采用安全的非拦截检测，因此原生后退、前进或中键动作仍会执行。"
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #909399; font-size: 12px;")
        layout.addWidget(description)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.chk_enabled = QCheckBox("启用全局划词翻译")
        self.chk_enabled.setChecked(settings.enabled)
        form.addRow("启用工具", self.chk_enabled)

        self.shortcut_button = ShortcutCaptureButton(settings.shortcut)
        self.shortcut_button.capture_error.connect(self._show_capture_error)
        form.addRow("翻译快捷键", self.shortcut_button)

        self.show_window_shortcut_button = ShortcutCaptureButton(
            settings.show_window_shortcut
        )
        self.show_window_shortcut_button.capture_error.connect(
            self._show_capture_error
        )
        form.addRow("显示窗口快捷键", self.show_window_shortcut_button)

        self.api_provider_combo = NoWheelComboBox()
        for provider in api_provider_options():
            self.api_provider_combo.addItem(
                provider.display_name,
                provider.provider_id,
            )
        provider_index = self.api_provider_combo.findData(settings.api_provider)
        self.api_provider_combo.setCurrentIndex(max(0, provider_index))
        form.addRow("API 接口", self.api_provider_combo)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("输入新 API Key（留空不修改）")
        self.api_key_input.setClearButtonEnabled(True)
        form.addRow("API Key", self.api_key_input)
        layout.addLayout(form)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #909399; font-size: 12px;")
        self.api_provider_combo.currentIndexChanged.connect(
            self._on_api_provider_changed
        )
        self._refresh_api_key_status()
        layout.addWidget(self.status_label)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(button_box)

    def _show_capture_error(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #E6A23C; font-size: 12px;")

    def selected_api_provider(self) -> str:
        provider_id = self.api_provider_combo.currentData()
        return str(provider_id or DEFAULT_API_PROVIDER)

    def _on_api_provider_changed(self, _index: int) -> None:
        self.api_key_input.clear()
        self._refresh_api_key_status()

    def _refresh_api_key_status(self) -> None:
        provider = get_api_provider(self.selected_api_provider())
        self.api_key_input.setPlaceholderText(
            f"输入新的 {provider.display_name} API Key（留空不修改）"
        )

        if provider.provider_id != self._initial_api_provider:
            message = (
                f"将切换到 {provider.display_name}。留空时会使用该接口已有的凭据或"
                f" {provider.environment_variable}；若均不存在，下次请求时会提示输入。"
            )
        elif self._environment_key_active:
            message = (
                f"当前 API Key 来自 {provider.environment_variable} 环境变量；"
                "它会在下次启动时优先于已保存的 Key。"
            )
        elif self._api_key_configured:
            message = "已设置 API Key。为保护密钥，输入框不会回填原值。"
        else:
            message = "尚未设置 API Key；新 Key 将安全保存到 Windows 凭据管理器。"

        if not provider.supports_vision:
            message += " 此接口当前仅用于文本翻译和查词，不支持图片 OCR。"
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #909399; font-size: 12px;")

    def candidate_settings(self) -> AppSettings:
        return AppSettings(
            enabled=self.chk_enabled.isChecked(),
            shortcut=self.shortcut_button.binding,
            show_window_shortcut=self.show_window_shortcut_button.binding,
            api_provider=self.selected_api_provider(),
        )

    def api_key(self) -> str:
        return self.api_key_input.text().strip()

    def done(self, result: int) -> None:
        self.shortcut_button.cancel_capture()
        self.show_window_shortcut_button.cancel_capture()
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
        binding: ShortcutBinding | None = None,
        *,
        hotkey_id: int = TRANSLATION_HOTKEY_ID,
        log_name: str = "translation",
    ):
        super().__init__(host_window)
        self._host_window = host_window
        self._binding = binding or DEFAULT_SETTINGS.shortcut
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
    def binding(self) -> ShortcutBinding:
        return self._binding

    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(self, binding: ShortcutBinding, enabled: bool) -> bool:
        if not isinstance(binding, ShortcutBinding):
            return False
        requested_enabled = bool(enabled)
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
        should_be_active = self._enabled and self._suspend_depth == 0
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
        if not self._binding.mouse_button:
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
        if self._host_window is None or self._binding.virtual_key is None:
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
                message = (
                    "[未设置 API Key，请先在窗口中发起一次翻译并输入 Key]"
                    if result.reason is RejectionReason.MISSING_KEY
                    else "[OCR 正忙，请稍后重试]"
                )
                self._window.set_result_message(message)
        except Exception:
            result = ReceiveResult.reject(
                RejectionReason.HANDLER_FAILED
            )
            record_event(AppEvent.IPC_REQUEST_REJECTED)
        finally:
            completion.complete(result)


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

        self.source_mode = "manual"         # manual / snipdo
        self.content_mode_override = "auto"  # auto / dictionary
        self.dictionary_source_lang = "auto"
        self.dictionary_target_lang = "default"
        self.pending_snipdo_text = ""
        self.original_paragraphs = []
        self.full_translation = ""
        self.force_quit = False
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
        self.shortcut_manager = None
        self.show_window_shortcut_manager = None
        self.xbutton1_hook = None
        self.current_request_is_structured = False
        self.active_translation_text = ""
        self.active_translation_mode = ""
        self.translation_history = self.load_translation_history()

        self.init_ui()
        self.setup_result_format()
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
        tray_menu.setStyleSheet("""
            QMenu {
                background-color: #FFFFFF;
                color: #303133;
                border: 1px solid #DCDFE6;
                padding: 5px;
            }
            QMenu::item {
                background-color: transparent;
                color: #303133;
                padding: 6px 22px 6px 10px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #EDE7F6;
                color: #303133;
            }
            QMenu::item:disabled {
                background-color: transparent;
                color: #A8ABB2;
            }
            QMenu::separator {
                height: 1px;
                background-color: #E4E7ED;
                margin: 4px 6px;
            }
        """)

        self.show_window_action = QAction("显示主窗口", self)
        self.show_window_action.triggered.connect(self.show_manual_window)
        tray_menu.addAction(self.show_window_action)

        self.toggle_translation_action = QAction("禁用", self)
        self.toggle_translation_action.setCheckable(True)
        self.toggle_translation_action.setChecked(not self.app_settings.enabled)
        self.toggle_translation_action.triggered.connect(
            self.toggle_global_translation
        )
        tray_menu.addAction(self.toggle_translation_action)

        settings_action = QAction("设置…", self)
        settings_action.triggered.connect(self.show_settings)
        tray_menu.addAction(settings_action)

        ocr_action = QAction("OCR 剪贴板图片", self)
        ocr_action.triggered.connect(self.start_clipboard_ocr)
        tray_menu.addAction(ocr_action)

        tray_menu.addSeparator()

        quit_action = QAction("彻底退出", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.messageClicked.connect(self.on_tray_message_clicked)
        self.tray_icon.show()

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
            tooltip += " · 有未读译文"
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
        try:
            self.tray_icon.showMessage(
                "翻译完成",
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
                "快捷键注册失败；请在设置中选择其他快捷键。",
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
            True,
        ):
            self.tray_icon.showMessage(
                APP_DISPLAY_NAME,
                "显示窗口快捷键注册失败；请在设置中选择其他快捷键。",
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
            tooltip = f"{APP_DISPLAY_NAME} · 全局划词翻译已禁用"
        elif shortcut_active:
            tooltip = f"{APP_DISPLAY_NAME} · {self.app_settings.shortcut.display}"
        else:
            tooltip = f"{APP_DISPLAY_NAME} · 快捷键未激活"
        show_shortcut_active = bool(
            getattr(self, "show_window_shortcut_manager", None)
            and self.show_window_shortcut_manager.is_installed()
        )
        show_shortcut_status = (
            self.app_settings.show_window_shortcut.display
            if show_shortcut_active
            else "未激活"
        )
        tooltip += f" · 显示窗口 {show_shortcut_status}"
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
            self.btn_settings.setText(self.app_settings.shortcut.display)
            self.btn_settings.setToolTip(tooltip + "；点击打开设置")
        if hasattr(self, "show_window_action"):
            self.show_window_action.setText(
                "显示主窗口（"
                f"{self.app_settings.show_window_shortcut.display}）"
            )

    def refresh_api_status(self):
        if not hasattr(self, "lbl_model_name"):
            return
        provider = get_api_provider(self.app_settings.api_provider)
        self.lbl_model_name.setText(
            f"{provider.short_name} · {provider.model}"
        )
        self.lbl_model_name.setToolTip(
            f"当前接口：{provider.display_name}\n"
            f"服务地址：{provider.base_url}\n"
            f"模型：{provider.model}"
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
        if not self.app_settings.enabled:
            return
        if self.selection_capture_busy:
            log("[Shortcut] capture skipped: busy")
            return

        self.selection_capture_busy = True
        self.suspend_xbutton1_hook()
        # The mouse hook suppresses the native button action.  Give the
        # foreground application one event-loop turn to settle focus before
        # sending Ctrl+C for the existing selection.
        QTimer.singleShot(35, self.translate_current_selection)

    def capture_current_selection_text(self) -> str:
        clipboard = QApplication.clipboard()
        original_mime = clone_clipboard_mime_data()
        sentinel = f"__gptsapi_selection_probe_{uuid.uuid4()}__"

        try:
            clipboard.setText(sentinel)
            QApplication.processEvents()
            sentinel_sequence = get_clipboard_sequence_number()

            send_ctrl_c()

            start_time = time.monotonic()
            quick_deadline = start_time + 0.28
            full_deadline = start_time + 0.65
            captured_text = ""
            clipboard_changed = False

            while time.monotonic() < (full_deadline if clipboard_changed else quick_deadline):
                QApplication.processEvents()
                if get_clipboard_sequence_number() != sentinel_sequence:
                    clipboard_changed = True

                current_text = clipboard_mime_to_formatted_text(
                    clipboard.mimeData(),
                    sentinel,
                )

                if current_text and current_text != sentinel:
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
            if not self.app_settings.enabled:
                return
            log("[Shortcut] triggered")
            selected_text = self.capture_current_selection_text()

            if not selected_text:
                log("[Shortcut] no selected text captured")
                self.tray_icon.showMessage(
                    APP_DISPLAY_NAME,
                    "未检测到选中文字，请先选择文本后再按快捷键。",
                    QSystemTrayIcon.MessageIcon.Information,
                    1800,
                )
                return

            self.handle_new_request(selected_text)
        except Exception as e:
            log(f"[Shortcut] translate_current_selection error: {e}")
        finally:
            self.selection_capture_busy = False
            QTimer.singleShot(150, self.restore_xbutton1_hook)

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

    def show_settings(self):
        self.force_show_window()
        provider = get_api_provider(self.app_settings.api_provider)
        dialog = SettingsDialog(
            self.app_settings,
            api_key_configured=(
                client is not None
                and api_runtime.provider.provider_id == provider.provider_id
            ),
            environment_key_active=not is_placeholder_api_key(
                os.getenv(provider.environment_variable, "")
            ),
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
        self.apply_settings(dialog.candidate_settings(), dialog.api_key())

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
            QMessageBox.warning(
                self,
                "快捷键冲突",
                "翻译快捷键和显示窗口快捷键不能相同。",
            )
            return False

        provider_changed = candidate.api_provider != previous.api_provider
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
            )
            if candidate_client is None:
                QMessageBox.warning(
                    self,
                    "API Key 无效",
                    "请输入有效的 API Key；其他设置尚未更改。",
                )
                return False
        elif provider_changed:
            selected_key = provider_api_key(
                candidate.api_provider,
                target_store,
            )
            if selected_key:
                candidate_client = create_api_client(
                    selected_key,
                    candidate.api_provider,
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
                QMessageBox.warning(
                    self,
                    "接口暂未切换",
                    "当前请求尚未结束。请稍后再次保存接口设置。",
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
            QMessageBox.warning(
                self,
                "快捷键不可用",
                "该快捷键可能已被其他程序占用。原快捷键仍保持激活。",
            )
            self.refresh_shortcut_status()
            return False

        if show_window_manager and not show_window_manager.configure(
            candidate.show_window_shortcut,
            True,
        ):
            if translation_manager:
                translation_manager.configure(
                    previous.shortcut,
                    previous.enabled,
                )
            QMessageBox.warning(
                self,
                "显示窗口快捷键不可用",
                "该快捷键可能已被其他程序占用。原快捷键仍保持激活。",
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
                    True,
                )
            QMessageBox.warning(
                self,
                "设置保存失败",
                "无法保存设置，已恢复原来的启用状态和快捷键。",
            )
            self.refresh_shortcut_status()
            return False

        self.app_settings = candidate
        self.refresh_shortcut_status()
        refresh_api_status = getattr(self, "refresh_api_status", None)
        if callable(refresh_api_status):
            refresh_api_status()

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
                activate_api_runtime(candidate.api_provider, candidate_client)
                record_event(AppEvent.CREDENTIAL_AVAILABLE)
            else:
                if provider_changed:
                    self.credential_store = target_store
                    activate_api_runtime(candidate.api_provider, None)
                record_event(AppEvent.CREDENTIAL_UNAVAILABLE)
                QMessageBox.warning(
                    self,
                    "API Key 保存失败",
                    "接口类型、启用状态和快捷键已保存，但 API Key 无法写入 Windows 凭据管理器。",
                )
                return False
        elif provider_changed:
            self.credential_store = target_store
            activate_api_runtime(candidate.api_provider, candidate_client)
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
            if not was_visible:
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

    # ---------- UI ----------
    def init_ui(self):
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setMinimumSize(NORMAL_WINDOW_WIDTH, NORMAL_WINDOW_HEIGHT)
        self.resize(NORMAL_WINDOW_WIDTH, NORMAL_WINDOW_HEIGHT)
        self.setStyleSheet("background-color: #F5F7FA;")

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
        self.card_frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-radius: 12px;
                border: 1px solid #EAEAEA;
            }
        """)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setYOffset(4)
        shadow.setColor(QColor(0, 0, 0, 12))
        self.card_frame.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card_frame)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)

        header_layout = QHBoxLayout()

        self.lbl_origin = QLabel("ORIGINAL (AUTO DETECT)")
        self.lbl_origin.setStyleSheet("color: #909399; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        header_layout.addWidget(self.lbl_origin)

        header_layout.addStretch()

        self.btn_content_mode = QPushButton("模式: Auto")
        self.btn_content_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_content_mode.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #E6A23C;
                border: none;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #F0B95E;
            }
        """)
        self.btn_content_mode.clicked.connect(self.toggle_content_mode)
        header_layout.addWidget(self.btn_content_mode)

        self.lbl_model_name = QLabel()
        self.lbl_model_name.setStyleSheet("""
            QLabel {
                background-color: #F5F7FA;
                color: #909399;
                border: 1px solid #E4E7ED;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
                font-weight: 600;
            }
        """)
        self.refresh_api_status()
        header_layout.addWidget(self.lbl_model_name)

        self.btn_settings = QPushButton(self.app_settings.shortcut.display)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setToolTip("设置（按钮文字为当前全局翻译快捷键）")
        self.btn_settings.setStyleSheet("""
            QPushButton {
                background-color: #F5F7FA;
                color: #606266;
                border: 1px solid #E4E7ED;
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                color: #8E44AD;
                border-color: #C39BD3;
            }
        """)
        self.btn_settings.clicked.connect(self.show_settings)
        header_layout.addWidget(self.btn_settings)

        self.lbl_dictionary_lang = QLabel("语言")
        self.lbl_dictionary_lang.setStyleSheet("color: #909399; font-size: 11px; font-weight: 600;")
        self.lbl_dictionary_lang.setToolTip("原文语言与译文/释义语言")

        combo_style = """
            QComboBox {
                background-color: #FAFAFA;
                color: #606266;
                border: 1px solid #DCDFE6;
                border-radius: 4px;
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
        self.cbo_dictionary_source.setToolTip("原文语言")
        for value, label in DICTIONARY_SOURCE_LANGUAGES:
            self.cbo_dictionary_source.addItem(label, value)
        self.cbo_dictionary_source.currentIndexChanged.connect(self.on_dictionary_language_changed)

        self.lbl_dictionary_arrow = QLabel("→")
        self.lbl_dictionary_arrow.setStyleSheet("color: #C0C4CC; font-size: 12px;")

        self.cbo_dictionary_target = NoWheelComboBox()
        self.cbo_dictionary_target.setStyleSheet(combo_style)
        self.cbo_dictionary_target.setFixedWidth(82)
        self.cbo_dictionary_target.setToolTip("译文/释义语言")
        for value, label in DICTIONARY_TARGET_LANGUAGES:
            self.cbo_dictionary_target.addItem(label, value)
        self.cbo_dictionary_target.currentIndexChanged.connect(self.on_dictionary_language_changed)

        card_layout.addLayout(header_layout)

        self.txt_origin = InteractiveTextEdit()
        self.txt_origin.submit_signal.connect(self.start_manual_translation)
        self.txt_origin.selectionChanged.connect(lambda: self.on_text_selection_changed("origin"))
        self.txt_origin.setMaximumHeight(180)

        origin_font = QFont()
        origin_font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        origin_font.setPixelSize(14)
        self.txt_origin.setFont(origin_font)
        self.txt_origin.setStyleSheet("""
            background-color: #FAFAFA;
            border-left: 2px solid #E4E7ED;
            padding-left: 6px;
            color: #303133;
        """)
        card_layout.addWidget(self.txt_origin)

        result_header_layout = QHBoxLayout()
        result_header_layout.setContentsMargins(0, 2, 0, 0)
        result_header_layout.setSpacing(8)

        self.lbl_result = QLabel("AUTO TRANSLATION")
        self.lbl_result.setStyleSheet("color: #8E44AD; font-size: 10px; font-weight: 700; letter-spacing: 1px; margin-top: 2px;")
        result_header_layout.addWidget(self.lbl_result)

        result_header_layout.addStretch()
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
        self.txt_result.setStyleSheet("background-color: transparent;")
        card_layout.addWidget(self.txt_result)

        self.btn_align_selection = QPushButton(self.card_frame)
        self.btn_align_selection.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_align_selection.setFixedHeight(26)
        self.btn_align_selection.setStyleSheet("""
            QPushButton {
                background-color: #2C3E50;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 3px 10px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #34495E;
            }
        """)
        self.btn_align_selection.clicked.connect(self.highlight_selected_counterpart)
        self.btn_align_selection.hide()

        main_layout.addWidget(self.card_frame)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 10, 0, 0)
        btn_layout.setSpacing(10)
        btn_layout.addStretch(1)

        self.btn_clear_origin = QPushButton("Clear")
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
        btn_layout.addWidget(self.btn_clear_origin)

        self.btn_ocr_clipboard = QPushButton("OCR")
        self.btn_ocr_clipboard.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ocr_clipboard.setToolTip("识别剪贴板图片文字，并自动翻译")
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

        self.btn_history = QPushButton("History")
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

        self.btn_translate = QPushButton("Translate (Ctrl+Enter)")
        self.btn_translate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_translate.clicked.connect(self.start_manual_translation)
        self.btn_translate.setStyleSheet("""
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
            QPushButton:disabled {
                background-color: #F2F3F5;
                color: #C0C4CC;
            }
        """)
        btn_layout.addWidget(self.btn_translate)

        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setEnabled(False)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #8E44AD;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #9B59B6;
            }
            QPushButton:disabled {
                background-color: #C39BD3;
            }
        """)
        btn_layout.addWidget(self.btn_copy)

        self.btn_hide = QPushButton("Hide")
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
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setWindowOpacity(1.0)

    def setup_result_format(self):
        if hasattr(self, "btn_align_selection"):
            self.reset_alignment_ui()

        self.txt_result.clear()

        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(15)

        self.result_char_fmt = QTextCharFormat()
        self.result_char_fmt.setFont(font)
        self.result_char_fmt.setForeground(QColor("#2c3e50"))

        self.result_block_fmt = QTextBlockFormat()
        self.result_block_fmt.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        self.result_block_fmt.setBottomMargin(12)

    def apply_markdown_document_style(self, widget, source_name: str):
        text_color = "#606266" if source_name == "origin" else "#2c3e50"
        if source_name == "origin":
            font_pixel_size = 13 if self.source_mode == "snipdo" else 14
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
        text_color = QColor("#606266" if source_name == "origin" else "#2C3E50")
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
                    if fragment.isValid():
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

    def render_markdown_text(self, widget, markdown_text: str, source_name: str) -> bool:
        markdown_text = (markdown_text or "").strip()
        if not markdown_text:
            widget.clear()
            return True

        try:
            self.apply_markdown_document_style(widget, source_name)
            document_text = markdown_text
            formula_fragments = []
            if source_name == "result":
                document_text, formula_fragments = (
                    prepare_latex_math_for_document(markdown_text)
                )

            if (
                is_latex_text(markdown_text)
                and not is_markdown_structured_text(markdown_text)
            ):
                widget.setPlainText(document_text)
            else:
                widget.setMarkdown(document_text)
                self.compact_markdown_list_indents(widget)

            if formula_fragments:
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
        menu.setStyleSheet("""
            QMenu {
                background-color: #FFFFFF;
                color: #303133;
                border: 1px solid #DCDFE6;
                border-radius: 6px;
                padding: 4px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 12px;
            }
            QMenu::item {
                color: #303133;
                background-color: transparent;
                padding: 6px 22px 6px 10px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                color: #303133;
                background-color: #EDE7F6;
            }
            QMenu::item:disabled {
                color: #A8ABB2;
            }
            QMenu::separator {
                height: 1px;
                background-color: #E4E7ED;
                margin: 4px 6px;
            }
        """)

        if not self.translation_history:
            empty_action = QAction("No history", self)
            empty_action.setEnabled(False)
            menu.addAction(empty_action)
        else:
            for entry in self.translation_history[:12]:
                title = f"{entry.get('time', '')}  {self.history_preview(entry.get('source', ''))}"
                action = QAction(title, self)
                action.triggered.connect(lambda _checked=False, item=entry: self.load_history_entry(item))
                menu.addAction(action)

            menu.addSeparator()
            clear_action = QAction("Clear history", self)
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
        self.full_translation = result_text
        self.setup_result_format()
        if result_text:
            self.render_markdown_text(self.txt_result, result_text, "result")
            self.btn_copy.setEnabled(True)
        self.force_show_window()

    def ensure_api_key(self, *, allow_prompt: bool = True) -> bool:
        provider = get_api_provider(self.app_settings.api_provider)
        if (
            client is not None
            and api_runtime.provider.provider_id == provider.provider_id
        ):
            return True
        if not allow_prompt:
            record_event(AppEvent.CREDENTIAL_MISSING)
            return False

        self.force_show_window()
        api_key, ok = QInputDialog.getText(
            self,
            f"输入 {provider.short_name} API Key",
            f"未检测到 {provider.display_name} API Key，请手动输入"
            "（将保存到 Windows 凭据管理器）：",
            QLineEdit.EchoMode.Password
        )

        if ok and configure_api_client(api_key, provider.provider_id):
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

    def set_result_message(self, message: str):
        self.setup_result_format()
        cursor = self.txt_result.textCursor()
        cursor.insertText(message, self.result_char_fmt)

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
            self.set_result_message(f"[OCR 出错: {e}]")

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
                f"[当前 {provider.short_name} 接口不支持图片 OCR；"
                "请在设置中切换到支持图片的接口]"
            )
            record_event(AppEvent.OCR_FAILED)
            return ReceiveResult.reject(RejectionReason.STARTUP_FAILED)

        # Deletion belongs exclusively to the launching process after an
        # accepted ACK.  The primary must first own an in-memory copy.
        if self.ocr_thread and self.ocr_thread.isRunning():
            record_event(AppEvent.OCR_BUSY)
            return ReceiveResult.reject(RejectionReason.BUSY)
        if not self.ensure_api_key(allow_prompt=allow_key_prompt):
            self.set_result_message("[未设置 API Key，已取消 OCR]")
            return ReceiveResult.reject(RejectionReason.MISSING_KEY)

        try:
            image_data_url = image_file_to_data_url(file_path)
        except Exception:
            self.force_show_window()
            self.set_result_message("[OCR 出错: 无法读取图片]")
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
                f"[当前 {provider.short_name} 接口不支持图片 OCR；"
                "请在设置中切换到支持图片的接口]"
            )
            record_event(AppEvent.OCR_FAILED)
            return False

        if not self.ensure_api_key(allow_prompt=allow_key_prompt):
            self.set_result_message("[未设置 API Key，已取消 OCR]")
            return False

        self.cancel_current_translation()
        self.ocr_result_source_mode = result_source_mode
        self.full_translation = ""
        self.original_paragraphs = []
        self.pending_snipdo_text = ""

        self.setup_result_format()
        self.txt_origin.clear()
        self.txt_origin.setReadOnly(True)
        self.lbl_origin.setText("OCR IMAGE")
        self.lbl_result.setText("OCR")
        self.btn_copy.setText("OCR...")
        self.btn_copy.setEnabled(False)

        if self.source_mode == "manual":
            self.btn_clear_origin.setEnabled(False)
            self.btn_translate.setEnabled(False)
            self.btn_translate.setText("OCR...")
            self.btn_ocr_clipboard.setEnabled(False)

        cursor = self.txt_result.textCursor()
        cursor.insertText("正在识别图片文字...", self.result_char_fmt)
        self.ensure_large_window_size()
        self.force_show_window()

        try:
            self.ocr_thread = OcrThread(image_data_url)
            self.ocr_thread.finished.connect(self.on_ocr_finished)
            self.ocr_thread.start()
        except Exception:
            self.ocr_thread = None
            self.set_result_message("[OCR 出错: 无法启动]")
            record_event(AppEvent.OCR_FAILED)
            return False

        record_event(AppEvent.OCR_STARTED)
        return True

    def on_ocr_finished(self, success: bool, extracted_text: str, error_msg: str):
        log(f"[UI] on_ocr_finished success={success}, error={error_msg}")

        if self.source_mode == "manual":
            self.btn_clear_origin.setEnabled(True)
            self.btn_translate.setEnabled(True)
            self.btn_translate.setText("Translate (Ctrl+Enter)")
            self.btn_ocr_clipboard.setEnabled(True)

        self.btn_copy.setText("Copy")
        self.btn_copy.setEnabled(False)

        if not success:
            record_event(AppEvent.OCR_FAILED)
            if error_msg != "已取消":
                self.set_result_message(f"[OCR 出错: {error_msg}]")
            return

        ocr_text = normalize_newlines(extracted_text).strip()
        if not ocr_text:
            record_event(AppEvent.OCR_FAILED)
            self.set_result_message("[OCR 出错: 未识别到文字]")
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

    def clear_manual_origin(self):
        if self.source_mode != "manual":
            return

        self.cancel_current_translation()
        self.txt_origin.clear()
        self.original_paragraphs = []
        self.full_translation = ""
        self.setup_result_format()
        self.btn_translate.setEnabled(True)
        self.btn_translate.setText("Translate (Ctrl+Enter)")
        self.btn_copy.setText("Copy")
        self.btn_copy.setEnabled(False)
        self.txt_origin.setFocus()

    def current_dictionary_languages(self):
        source_lang = self.cbo_dictionary_source.currentData() or "auto"
        target_lang = self.cbo_dictionary_target.currentData() or "default"
        self.dictionary_source_lang = source_lang
        self.dictionary_target_lang = target_lang
        return source_lang, target_lang

    def set_dictionary_language_controls_visible(self, visible: bool):
        self.lbl_dictionary_lang.setVisible(visible)
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
        return "ORIGINAL (AUTO DETECT)" if source_lang == "auto" else "ORIGINAL"

    def update_content_mode_button_text(self):
        if self.content_mode_override == "dictionary":
            self.btn_content_mode.setText("模式: 词典")
        else:
            self.btn_content_mode.setText("模式: Auto")

    def resolve_effective_mode(self, text: str) -> str:
        if self.content_mode_override == "dictionary":
            return "dictionary"
        if is_dictionary_mode(text):
            return "dictionary"
        source_lang, target_lang = self.current_dictionary_languages()
        return resolve_auto_translation_mode(text, source_lang, target_lang)

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
        self.btn_copy.setText("Looking up..." if effective_mode == "dictionary" else "Translating...")
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

    def alignment_feature_enabled(self):
        if self.source_mode != "snipdo":
            return False

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

        self.btn_align_selection.setText("定位译文" if source_name == "origin" else "定位原文")
        self.btn_align_selection.adjustSize()

        rect = source_widget.cursorRect(cursor)
        global_pos = source_widget.viewport().mapToGlobal(rect.topRight())
        pos = self.card_frame.mapFromGlobal(global_pos)

        margin = 6
        x = min(max(margin, pos.x() + 8), max(margin, self.card_frame.width() - self.btn_align_selection.width() - margin))
        y = min(max(margin, pos.y() - self.btn_align_selection.height() - 8), max(margin, self.card_frame.height() - self.btn_align_selection.height() - margin))

        self.btn_align_selection.move(x, y)
        self.btn_align_selection.show()
        self.btn_align_selection.raise_()

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
        selection.format.setBackground(QColor("#FFF1A8"))
        selection.format.setForeground(QColor("#1F2D3D"))
        widget.setExtraSelections([selection])

        view_cursor = widget.textCursor()
        view_cursor.setPosition(start)
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
        self.btn_align_selection.setText("定位中...")
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

    # ---------- 模式切换 ----------
    def apply_manual_mode_ui(self, reset_content=False):
        self.source_mode = "manual"
        self.reset_alignment_ui()

        self.btn_content_mode.show()
        self.btn_clear_origin.show()
        self.btn_ocr_clipboard.show()
        self.btn_ocr_clipboard.setEnabled(True)
        self.btn_translate.show()
        self.update_content_mode_button_text()
        self.set_dictionary_language_controls_visible(True)
        self.txt_origin.setReadOnly(False)
        self.txt_origin.setMaximumHeight(220)

        if self.content_mode_override == "dictionary":
            self.lbl_origin.setText("ORIGINAL")
            self.lbl_result.setText("DICTIONARY")
            self.txt_origin.setPlaceholderText("在此输入或粘贴需要查词的单词、短语或术语...\n按 Ctrl + Enter 开始查词")
        else:
            direction_mode = self.selected_translation_mode()
            self.lbl_origin.setText(self.selected_origin_label())
            self.lbl_result.setText(self.selected_translation_label(direction_mode))
            self.txt_origin.setPlaceholderText("在此输入或粘贴需要翻译的文本...\n按 Ctrl + Enter 开始翻译")

        if reset_content:
            self.original_paragraphs = []
            self.full_translation = ""
            self.txt_origin.clear()
            self.setup_result_format()
            self.btn_translate.setEnabled(True)
            self.btn_translate.setText("Translate (Ctrl+Enter)")
            self.btn_copy.setText("Copy")
            self.btn_copy.setEnabled(False)

    def apply_snipdo_mode_ui(self):
        self.source_mode = "snipdo"
        self.reset_alignment_ui()

        self.btn_content_mode.show()
        self.update_content_mode_button_text()
        self.btn_translate.hide()
        self.btn_clear_origin.hide()
        self.btn_ocr_clipboard.hide()
        self.set_dictionary_language_controls_visible(True)
        self.txt_origin.setReadOnly(True)
        self.txt_origin.setPlaceholderText("")

        total_text = "\n".join(self.original_paragraphs).strip()
        effective_mode = self.resolve_effective_mode(total_text)
        dict_mode = effective_mode == "dictionary"

        if dict_mode:
            self.lbl_origin.setText("ORIGINAL")
            self.lbl_result.setText("DICTIONARY")
        else:
            self.lbl_origin.setText(self.selected_origin_label())
            self.lbl_result.setText(self.selected_translation_label(effective_mode))

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

        for idx, para in enumerate(self.original_paragraphs):
            cursor.insertText(para, char_fmt)
            cursor.setBlockFormat(block_fmt)
            if idx != len(self.original_paragraphs) - 1:
                cursor.insertBlock()

        self.txt_origin.moveCursor(QTextCursor.MoveOperation.Start)

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
            self.btn_copy.setText("Looking up..." if effective_mode == "dictionary" else "Translating...")
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

        if effective_mode == "dictionary":
            self.set_dictionary_language_controls_visible(True)
            self.lbl_origin.setText("ORIGINAL")
            self.lbl_result.setText("DICTIONARY")
        else:
            self.set_dictionary_language_controls_visible(True)
            self.lbl_origin.setText(self.selected_origin_label())
            self.lbl_result.setText(self.selected_translation_label(effective_mode))

        self.btn_translate.setEnabled(False)
        self.btn_translate.setText("Looking up..." if effective_mode == "dictionary" else "Translating...")
        self.btn_copy.setEnabled(False)
        self.btn_copy.setText("Copy")

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
            cursor = self.txt_result.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText("[未设置 API Key，已取消翻译]", self.result_char_fmt)

            if self.source_mode == "manual":
                self.btn_translate.setEnabled(True)
                self.btn_translate.setText("Translate (Ctrl+Enter)")

            self.btn_copy.setText("Copy")
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
            self.set_result_message("[翻译出错: 无法启动]")
            record_event(AppEvent.TRANSLATION_FAILED)
            return False

        record_event(AppEvent.TRANSLATION_STARTED)
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
        cursor = self.txt_result.textCursor()

        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.setBlockFormat(self.result_block_fmt)
        cursor.insertText(chunk, self.result_char_fmt)

        self.txt_result.setTextCursor(cursor)
        self.txt_result.ensureCursorVisible()

    def on_translation_finished(self, success, error_msg):
        log(f"[UI] on_translation_finished success={success}, error={error_msg}")
        cursor = self.txt_result.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if not success:
            if error_msg == "已取消":
                record_event(AppEvent.TRANSLATION_CANCELLED)
            else:
                record_event(AppEvent.TRANSLATION_FAILED)
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText(f"\n\n[翻译出错: {error_msg}]", self.result_char_fmt)
        elif self.full_translation.strip():
            record_event(AppEvent.TRANSLATION_COMPLETED)
            self.render_markdown_text(self.txt_result, self.full_translation, "result")
            self.add_history_entry(
                self.active_translation_text,
                self.full_translation,
                self.active_translation_mode,
            )
            self.notify_translation_completed()
        else:
            record_event(AppEvent.TRANSLATION_FAILED)

        if self.source_mode == "manual":
            self.btn_translate.setEnabled(True)
            self.btn_translate.setText("Translate (Ctrl+Enter)")

        self.btn_copy.setText("Copy")
        self.btn_copy.setEnabled(bool(self.full_translation.strip()))

        if self.source_mode == "snipdo":
            self.ensure_large_window_size()

    # ---------- 剪贴板 ----------
    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        if self.full_translation:
            clipboard.setText(self.full_translation.strip())
            self.btn_copy.setText("Copied")
            QTimer.singleShot(900, lambda: self.btn_copy.setText("Copy"))
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
        )
        window = TranslationWindow(
            paths,
            store,
            initial_settings=startup_settings,
        )
        app.aboutToQuit.connect(window.shutdown_translation_shortcut)
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

    initial_result = window.handle_app_request(
        prepared.request,
        allow_key_prompt=True,
    )
    settle_source_file(prepared, accepted=initial_result.accepted)

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
