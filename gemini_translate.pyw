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
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote

from openai import OpenAI

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTextEdit,
    QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QSystemTrayIcon, QMenu, QInputDialog, QLineEdit,
    QComboBox
)
from PyQt6.QtGui import (
    QColor, QScreen, QTextCursor, QTextCharFormat,
    QTextBlockFormat, QTextFormat, QFont, QAction, QIcon
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject, QTimer, QByteArray, QBuffer, QIODevice, QMimeData
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
LOCAL_API_KEY_FILE = ".gptsapi_api_key"
LOCAL_API_KEY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    LOCAL_API_KEY_FILE,
)


def read_local_api_key() -> str:
    try:
        if not os.path.exists(LOCAL_API_KEY_PATH):
            return ""

        with open(LOCAL_API_KEY_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def save_local_api_key(api_key: str) -> bool:
    api_key = (api_key or "").strip()
    if not api_key:
        return False

    try:
        with open(LOCAL_API_KEY_PATH, "w", encoding="utf-8") as f:
            f.write(api_key + "\n")
        return True
    except Exception:
        return False


GPTSAPI_API_KEY = os.getenv("GPTSAPI_API_KEY", "").strip() or read_local_api_key()
client = None

# PROXY_URL = 'http://127.0.0.1:7897'
# os.environ['HTTPS_PROXY'] = PROXY_URL
# os.environ['HTTP_PROXY'] = PROXY_URL

MODEL_NAME = 'gpt-5.4-nano'
REQUEST_TIMEOUT_SECONDS = 15.0
SERVER_NAME = "gptsapi_translate_snipdo_single_instance_v1"
OCR_IMAGE_REQUEST_PREFIX = "__GPTSAPI_OCR_IMAGE__:"


def is_placeholder_api_key(api_key: str) -> bool:
    api_key = (api_key or "").strip().lower()
    return (
        not api_key
        or "填这里" in api_key
        or "your_" in api_key
        or "your-" in api_key
        or "api_key" in api_key
    )


def configure_api_client(api_key: str) -> bool:
    global GPTSAPI_API_KEY, client

    api_key = (api_key or "").strip()
    if is_placeholder_api_key(api_key):
        return False

    GPTSAPI_API_KEY = api_key
    os.environ["GPTSAPI_API_KEY"] = api_key
    client = OpenAI(
        api_key=GPTSAPI_API_KEY,
        base_url="https://api.gptsapi.net/v1",
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=1,
    )
    return True


configure_api_client(GPTSAPI_API_KEY)
# ===========================================


# ================= 调试日志 =================
def log(msg: str):
    try:
        log_file = os.path.join(os.getenv("TEMP", "."), "gemini_translate_debug.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# ================= Windows 前台显示工具 =================
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9
SW_SHOW = 5

WH_MOUSE_LL = 14
HC_ACTION = 0
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
XBUTTON1 = 0x0001
VK_CONTROL = 0x11
VK_C = 0x43
KEYEVENTF_KEYUP = 0x0002

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LRESULT = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


LowLevelMouseProc = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.c_int,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
)

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    LowLevelMouseProc,
    ctypes.c_void_p,
    ctypes.c_ulong,
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = ctypes.c_bool
kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p


def win32_force_foreground(hwnd: int) -> bool:
    """
    在 Windows 下尽量把指定窗口恢复并前置到前台。
    仅依赖 Qt 的 show()/raise_()/activateWindow() 在 pythonw + 托盘 + 外部唤起场景下不够稳定，
    因此这里额外调用 Win32 API 强制显示窗口。
    """
    try:
        if not hwnd:
            return False

        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception as e:
        log(f"[Win32] force foreground error: {e}")
        return False


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


def is_structured_text(text: str) -> bool:
    if not text:
        return False

    return bool(re.search(
        r'(?m)^\s*(#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s+|```|\|.*\|)',
        text,
    ))


def markdown_format_instruction(text: str) -> str:
    if not is_structured_text(text):
        return ""

    return """
格式要求：
- 输入文本包含 Markdown/结构化格式；请保留标题层级、段落、列表、表格、链接和代码块结构。
- 只翻译自然语言内容，不要翻译 Markdown 标记、URL、代码块、行内代码、变量名、函数名、文件路径和 HTML/XML 标签名。
- 如果输入是表格，请保持相同的列数和行数；只翻译单元格里的自然语言。
- 直接输出保留格式后的译文，不要解释你做了什么。
""".strip()


def clipboard_mime_to_formatted_text(mime_data, sentinel: str = "") -> str:
    if mime_data is None:
        return ""

    plain_text = mime_data.text().strip() if mime_data.hasText() else ""
    html_text = mime_data.html() if mime_data.hasHtml() else ""

    if html_text:
        markdown_text = html_to_markdown(html_text)
        if markdown_text and markdown_text != sentinel:
            if is_structured_text(markdown_text) or len(markdown_text) >= max(1, len(plain_text) // 2):
                return markdown_text.strip()

    if plain_text and plain_text != sentinel:
        return plain_text.strip()

    return ""


def build_ocr_image_request(file_path: str, delete_after: bool = False) -> str:
    delete_flag = "1" if delete_after else "0"
    return f"{OCR_IMAGE_REQUEST_PREFIX}{delete_flag}:{file_path}"


def parse_ocr_image_request(raw_text: str):
    if not raw_text.startswith(OCR_IMAGE_REQUEST_PREFIX):
        return None

    payload = raw_text[len(OCR_IMAGE_REQUEST_PREFIX):]
    delete_after = False

    if len(payload) >= 2 and payload[0] in ("0", "1") and payload[1] == ":":
        delete_after = payload[0] == "1"
        file_path = payload[2:]
    else:
        file_path = payload

    return file_path.strip(), delete_after


def normalize_input_text(raw_text: str):
    """
    处理来自 SnipDo 的原始文本，返回段落列表
    """
    if not raw_text:
        return []

    clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
    text_to_translate = unquote(clean_text)
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


def is_dictionary_mode(text: str):
    text_clean = re.sub(r'\s+', ' ', text.strip())
    if not text_clean or "\n" in text.strip():
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

    return len(text_clean) <= 10


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


def send_to_existing_instance(text: str, retries: int = 5, delay_ms: int = 180) -> bool:
    """
    尝试把文本发送给已运行的实例
    增加短重试，提升首启竞争阶段的成功率
    需要在 QApplication 创建之后调用
    """
    for attempt in range(1, retries + 1):
        socket = None
        try:
            log(f"[IPC] send attempt {attempt}/{retries}, text={repr(text[:200])}")
            socket = QLocalSocket()
            socket.connectToServer(SERVER_NAME)

            if not socket.waitForConnected(600):
                log(f"[IPC] connect failed on attempt {attempt}")
            else:
                data = text.encode("utf-8")
                socket.write(data)
                socket.flush()

                if socket.waitForBytesWritten(1000):
                    socket.disconnectFromServer()
                    log(f"[IPC] sent to existing instance successfully on attempt {attempt}")
                    return True
                else:
                    log(f"[IPC] waitForBytesWritten failed on attempt {attempt}")

        except Exception as e:
            log(f"[IPC] send_to_existing_instance error on attempt {attempt}: {e}")
        finally:
            try:
                if socket is not None:
                    socket.abort()
            except Exception:
                pass

        if attempt < retries:
            time.sleep(delay_ms / 1000)

    log("[IPC] failed to send to existing instance after retries")
    return False


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

    def request_stop(self):
        self._stop_requested = True

    def build_prompt(self) -> str:
        text_clean = self.text.strip()

        if self.mode == "dictionary":
            source_lang = dictionary_language_label(self.dictionary_source_lang)
            target_lang = dictionary_language_label(self.dictionary_target_lang, target=True)
            return f"""
你是一个专业的多语言词典助手。请根据用户选择的语言设置，对下面的单词或短语进行释义和对应表达整理，不要使用 markdown 语法。

语言设置：
1. 原文语言：{source_lang}。如果是 Auto，请先自动识别原文语言。
2. 释义/对应表达语言：{target_lang}。如果是 默认，请按“中文词语优先给英文对应表达；非中文词语优先用简体中文释义”的默认中英查词习惯处理。

请按以下结构输出：
1. 语言：写出原文语言；如果用户指定了原文语言，请按指定语言理解
2. 读音：给出常见读音、音标或罗马化（如果适用）
3. 对应表达：给出 1-3 个目标语言中自然、常用、贴切的表达
4. 释义：解释含义；如果目标语言不是 默认，请用所选目标语言解释
5. 用法：说明常见搭配、语气或使用场景
6. 例句：提供 1-2 个简短实用的双语例句

要求：
- 优先选择真实自然、常见的表达
- 如果是偏抽象概念，可给出意译而不是生硬直译
- 直接输出结果，不要加前缀，不要解释你在做什么

待查内容：
{text_clean}
"""

        source_lang = dictionary_language_label(self.dictionary_source_lang)
        target_lang = dictionary_language_label(self.dictionary_target_lang, target=True)
        uses_custom_language = self.dictionary_source_lang != "auto" or self.dictionary_target_lang != "default"

        actual_mode = self.mode
        if actual_mode == "auto":
            if uses_custom_language:
                return f"""
你是一个专业的多语言翻译引擎。请根据用户选择的语言设置翻译下方文本。

语言设置：
1. 原文语言：{source_lang}。如果是 Auto，请先自动识别原文语言。
2. 目标语言：{target_lang}。如果是 默认，请按默认规则处理：中文译英文，英文或其他语言译简体中文。

规则：
1. 追求信达雅：根据目标语言的表达习惯自由调整句式、段落和语序，确保译文流畅、自然、专业。
2. 直接输出译文，不要说明识别到的语言，不要添加前缀或解释。

待翻译文本：
{text_clean}
"""

            return f"""
你是一个专业的多语言翻译引擎。请先自动识别下方原文的主要语言，再按默认规则翻译：
1. 如果原文主要是中文（含简体或繁体），翻译成地道的英文。
2. 如果原文主要是英文，翻译成地道的简体中文。
3. 如果原文是其他语言，默认翻译成地道的简体中文；专有名词、代码、型号和品牌名按语境保留或自然处理。

规则：
1. 追求信达雅：根据目标语言的表达习惯自由调整句式、段落和语序，确保译文流畅、自然、专业。
2. 直接输出译文，不要说明识别到的语言，不要添加前缀或解释。

待翻译文本：
{text_clean}
"""

        if actual_mode == "zh2en":
            return f"""
你是一个专业的多语言翻译引擎。请将下方文本翻译成地道的英文。
原文语言设置：{source_lang}。如果是 Auto，请先自动识别原文语言。

规则：
1. 追求信达雅：根据英文母语者的表达习惯自由调整句式、段落和语序，确保译文流畅、自然、专业。
2. 直接输出译文，不要任何解释，不要加前缀。

待翻译文本：
{text_clean}
"""
        else:
            return f"""
你是一个专业的多语言翻译引擎。请将下方文本翻译成地道的简体中文。
原文语言设置：{source_lang}。如果是 Auto，请先自动识别原文语言。

规则：
1. 追求信达雅：根据中文表达习惯自由调整句式、段落和语序，确保译文流畅、自然、专业。
2. 直接输出译文，不要任何解释，不要加前缀。

待翻译文本：
{text_clean}
"""

    def run(self):
        try:
            if client is None:
                raise RuntimeError("未设置 GPTSAPI_API_KEY")

            prompt = self.build_prompt()
            format_instruction = markdown_format_instruction(self.text)
            if format_instruction and self.mode != "dictionary":
                prompt = f"{format_instruction}\n\n{prompt}"
            log(f"[TranslateThread] start, mode={self.mode}, text={repr(self.text[:200])}")

            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                stream=True
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
                        self.chunk_received.emit(content)
                except Exception:
                    continue

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
            if client is None:
                raise RuntimeError("未设置 GPTSAPI_API_KEY")

            log(f"[AlignmentThread] start, selected={repr(self.selected_text[:120])}")
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "user", "content": self.build_prompt()}
                ],
                stream=False,
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

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            if client is None:
                raise RuntimeError("未设置 GPTSAPI_API_KEY")

            log("[OcrThread] start")
            response = client.chat.completions.create(
                model=MODEL_NAME,
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


# ================= 3. 单实例本地通信服务 =================
class SingleInstanceServer(QObject):
    message_received = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.server = QLocalServer()

        if not self.server.listen(SERVER_NAME):
            log(f"[Server] first listen failed: {self.server.errorString()}")

            probe = QLocalSocket()
            probe.connectToServer(SERVER_NAME)
            if probe.waitForConnected(300):
                probe.disconnectFromServer()
                log("[Server] another instance is already running")
                raise RuntimeError("已有实例在运行")
            else:
                log("[Server] no running instance detected, removing stale server")
                QLocalServer.removeServer(SERVER_NAME)
                if not self.server.listen(SERVER_NAME):
                    log(f"[Server] second listen failed: {self.server.errorString()}")
                    raise RuntimeError(f"无法启动本地服务: {self.server.errorString()}")

        self.server.newConnection.connect(self.handle_new_connection)
        log("[Server] listen success")

    def handle_new_connection(self):
        log("[Server] new connection")
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self.read_socket_data(s))
            socket.disconnected.connect(socket.deleteLater)
            QTimer.singleShot(50, lambda s=socket: self.read_socket_data(s))

    def read_socket_data(self, socket):
        try:
            if socket is None:
                return
            if socket.bytesAvailable() <= 0:
                return

            data = bytes(socket.readAll()).decode("utf-8", errors="ignore")
            log(f"[Server] received data: {repr(data[:300])}")
            if data.strip():
                self.message_received.emit(data.strip())
        except Exception as e:
            log(f"[Server] read_socket_data error: {e}")
        finally:
            try:
                socket.disconnectFromServer()
            except Exception:
                pass


# ================= 4. 主窗口逻辑 =================
class XButton1MouseHook(QObject):
    triggered = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hook = None
        self._callback = None
        self._last_trigger_time = 0.0

    def install(self) -> bool:
        if self._hook:
            return True

        self._callback = LowLevelMouseProc(self._handle_mouse_event)
        module_handle = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            self._callback,
            module_handle,
            0,
        )

        if not self._hook:
            error_code = kernel32.GetLastError()
            log(f"[XButton1] install hook failed, error={error_code}")
            self._callback = None
            return False

        log("[XButton1] hook installed")
        return True

    def uninstall(self):
        if not self._hook:
            return

        try:
            user32.UnhookWindowsHookEx(self._hook)
            log("[XButton1] hook uninstalled")
        except Exception as e:
            log(f"[XButton1] uninstall hook error: {e}")
        finally:
            self._hook = None
            self._callback = None

    def _handle_mouse_event(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION and w_param in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                mouse_info = ctypes.cast(
                    l_param,
                    ctypes.POINTER(MSLLHOOKSTRUCT),
                ).contents
                xbutton = (mouse_info.mouseData >> 16) & 0xFFFF

                if xbutton == XBUTTON1:
                    if w_param == WM_XBUTTONUP:
                        now = time.monotonic()
                        if now - self._last_trigger_time >= 0.25:
                            self._last_trigger_time = now
                            self.triggered.emit()

                    return 1
        except Exception as e:
            log(f"[XButton1] hook callback error: {e}")

        return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)


class TranslationWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.source_mode = "manual"         # manual / snipdo
        self.content_mode_override = "auto"  # auto / translate / dictionary
        self.dictionary_source_lang = "auto"
        self.dictionary_target_lang = "default"
        self.pending_snipdo_text = ""
        self.original_paragraphs = []
        self.full_translation = ""
        self.force_quit = False
        self.trans_thread = None
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
        self.xbutton1_hook = None
        self.current_request_is_structured = False

        self.init_ui()
        self.setup_result_format()
        self.setup_tray_icon()
        self.setup_xbutton1_hook()
        self.apply_manual_mode_ui()
        log(f"[UI] TranslationWindow initialized, hwnd={int(self.winId())}")

    # ---------- 托盘 ----------
    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)

        current_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(current_dir, "snipdo_script_logo", "gemini-color.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else self.style().standardIcon(
            self.style().StandardPixmap.SP_ComputerIcon
        )

        self.tray_icon.setIcon(icon)
        self.setWindowIcon(icon)
        self.tray_icon.setToolTip("Gemini 翻译")

        tray_menu = QMenu()

        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_manual_window)
        tray_menu.addAction(show_action)

        ocr_action = QAction("OCR 剪贴板图片", self)
        ocr_action.triggered.connect(self.start_clipboard_ocr)
        tray_menu.addAction(ocr_action)

        tray_menu.addSeparator()

        quit_action = QAction("彻底退出", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def setup_xbutton1_hook(self):
        self.xbutton1_hook = XButton1MouseHook(self)
        self.xbutton1_hook.triggered.connect(self.on_xbutton1_triggered)

        if self.xbutton1_hook.install():
            self.tray_icon.setToolTip("Gemini Translate - XButton1")
        else:
            self.tray_icon.showMessage(
                "Gemini Translate",
                "XButton1 hook failed",
                QSystemTrayIcon.MessageIcon.Warning,
                1800,
            )

    def on_xbutton1_triggered(self):
        if self.selection_capture_busy:
            log("[XButton1] capture skipped: busy")
            return

        self.selection_capture_busy = True
        QTimer.singleShot(30, self.translate_current_selection)

    def capture_current_selection_text(self) -> str:
        clipboard = QApplication.clipboard()
        original_mime = clone_clipboard_mime_data()
        sentinel = f"__gptsapi_selection_probe_{uuid.uuid4()}__"

        try:
            clipboard.setText(sentinel)
            QApplication.processEvents()

            send_ctrl_c()

            deadline = time.monotonic() + 0.65
            captured_text = ""

            while time.monotonic() < deadline:
                QApplication.processEvents()
                current_text = clipboard_mime_to_formatted_text(
                    clipboard.mimeData(),
                    sentinel,
                )

                if current_text and current_text != sentinel:
                    captured_text = current_text
                    break

                time.sleep(0.03)

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
            log("[XButton1] triggered")
            selected_text = self.capture_current_selection_text()

            if not selected_text:
                log("[XButton1] no selected text captured")
                if self.tray_icon.isVisible():
                    self.tray_icon.showMessage(
                        "Gemini Translate",
                        "No selected text",
                        QSystemTrayIcon.MessageIcon.Information,
                        1000,
                    )
                return

            self.handle_new_request(selected_text)
        except Exception as e:
            log(f"[XButton1] translate_current_selection error: {e}")
        finally:
            self.selection_capture_busy = False

    def on_tray_activated(self, reason):
        log(f"[Tray] activated: {reason}")
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick
        ):
            self.show_manual_window()

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
        if self.xbutton1_hook:
            self.xbutton1_hook.uninstall()
        self.tray_icon.hide()
        QApplication.quit()

    def cancel_current_translation(self):
        if self.trans_thread and self.trans_thread.isRunning():
            log("[UI] cancel_current_translation")
            self.trans_thread.request_stop()
            self.trans_thread.wait(800)

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
            screen = QApplication.primaryScreen()
            if screen:
                available = screen.availableGeometry()
                x = available.x() + max(40, (available.width() - self.width()) // 2)
                y = available.y() + max(40, (available.height() - self.height()) // 2)
                self.move(x, y)

            self.showNormal()
            self.setWindowState(Qt.WindowState.WindowNoState)
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
            self.showNormal()
            self.setWindowState(Qt.WindowState.WindowNoState)
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
        self.setWindowTitle("Gemini 翻译")
        self.resize(560, 680)
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

        self.lbl_model_name = QLabel(f"Model: {MODEL_NAME}")
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
        self.lbl_model_name.setToolTip("当前使用的模型")
        header_layout.addWidget(self.lbl_model_name)

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
            widget.setMarkdown(markdown_text)
            self.compact_markdown_list_indents(widget)
            self.apply_markdown_block_formats(widget, source_name)
            widget.moveCursor(QTextCursor.MoveOperation.Start)
            return True
        except Exception as e:
            log(f"[Markdown] render failed for {source_name}: {e}")
            widget.setPlainText(markdown_text)
            return False

    def ensure_api_key(self) -> bool:
        if client is not None:
            return True

        self.force_show_window()
        api_key, ok = QInputDialog.getText(
            self,
            "输入 API Key",
            "未检测到 GPTSAPI_API_KEY，请手动输入：",
            QLineEdit.EchoMode.Password
        )

        if ok and configure_api_client(api_key):
            if save_local_api_key(api_key):
                log(f"[UI] API key saved to {LOCAL_API_KEY_FILE}")
            else:
                log(f"[UI] API key configured but failed to save {LOCAL_API_KEY_FILE}")
            log("[UI] API key configured from manual input")
            return True

        log("[UI] API key input cancelled or empty")
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

    def start_image_file_ocr(self, file_path: str, delete_after: bool = False):
        log(f"[UI] start_image_file_ocr file={file_path}, delete_after={delete_after}")

        try:
            image_data_url = image_file_to_data_url(file_path)
        except Exception as e:
            log(f"[UI] read OCR image error: {e}")
            self.force_show_window()
            self.set_result_message(f"[OCR 出错: 无法读取图片 {e}]")
            return
        finally:
            if delete_after:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as e:
                    log(f"[UI] remove OCR image error: {e}")

        self.start_ocr(image_data_url, result_source_mode="snipdo")

    def start_ocr(self, image_data_url: str, result_source_mode: str):
        if self.ocr_thread and self.ocr_thread.isRunning():
            log("[UI] OCR already running")
            return

        if not self.ensure_api_key():
            self.set_result_message("[未设置 API Key，已取消 OCR]")
            return

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
        self.resize(560, 520)
        self.force_show_window()

        self.ocr_thread = OcrThread(image_data_url)
        self.ocr_thread.finished.connect(self.on_ocr_finished)
        self.ocr_thread.start()

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
            if error_msg != "已取消":
                self.set_result_message(f"[OCR 出错: {error_msg}]")
            return

        ocr_text = normalize_newlines(extracted_text).strip()
        if not ocr_text:
            self.set_result_message("[OCR 出错: 未识别到文字]")
            return

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
        elif self.content_mode_override == "translate":
            self.btn_content_mode.setText("模式: 翻译")
        else:
            self.btn_content_mode.setText("模式: Auto")

    def resolve_effective_mode(self, text: str) -> str:
        direction_mode = self.selected_translation_mode()

        if self.content_mode_override == "dictionary":
            return "dictionary"
        if self.content_mode_override == "translate":
            return direction_mode
        if is_dictionary_mode(text):
            return "dictionary"
        return direction_mode

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
        order = ["auto", "translate", "dictionary"]
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

    def on_dictionary_language_changed(self, *_args):
        self.current_dictionary_languages()

        if self.source_mode == "snipdo":
            self.rerun_snipdo_translation()
        else:
            self.apply_manual_mode_ui(reset_content=False)

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

    def adjust_window_height(self):
        total_text = "\n".join(self.original_paragraphs).strip()
        text_len = len(total_text)
        dict_mode = self.resolve_effective_mode(total_text) == "dictionary"

        base_width = 560

        if dict_mode:
            base_height = 700
        elif text_len < 50:
            base_height = 380
        elif text_len < 200:
            base_height = 520
        else:
            base_height = 700

        self.resize(base_width, base_height)

    # ---------- 请求入口 ----------
    def handle_new_request(self, raw_text):
        try:
            log(f"[UI] handle_new_request received: {repr(raw_text[:300])}")
            ocr_request = parse_ocr_image_request(raw_text)
            if ocr_request:
                file_path, delete_after = ocr_request
                self.start_image_file_ocr(file_path, delete_after)
                return

            self.cancel_current_ocr()
            self.cancel_current_translation()

            self.original_paragraphs = normalize_input_text(raw_text)
            log(f"[UI] normalized paragraphs count={len(self.original_paragraphs)}")

            if not self.original_paragraphs:
                log("[UI] no valid paragraphs after normalize_input_text")
                return

            total_text = "\n".join(self.original_paragraphs).strip()
            self.pending_snipdo_text = total_text
            self.current_request_is_structured = is_structured_text(total_text)

            effective_mode = self.resolve_effective_mode(total_text)

            self.full_translation = ""
            self.btn_copy.setText("Looking up..." if effective_mode == "dictionary" else "Translating...")
            self.btn_copy.setEnabled(False)

            log(f"[UI] effective_mode={effective_mode}, total_text={repr(total_text[:300])}")

            self.apply_snipdo_mode_ui()
            self.adjust_window_height()
            self.populate_original_text()
            self.setup_result_format()

            self.force_show_window()

            QTimer.singleShot(120, lambda: self.start_translation(total_text, effective_mode))
        except Exception as e:
            log(f"[UI] handle_new_request error: {e}")

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
        self.adjust_window_height()

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
    ):
        log(f"[UI] start_translation, mode={mode}, text={repr(text[:300])}")

        if not self.ensure_api_key():
            cursor = self.txt_result.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText("[未设置 API Key，已取消翻译]", self.result_char_fmt)

            if self.source_mode == "manual":
                self.btn_translate.setEnabled(True)
                self.btn_translate.setText("Translate (Ctrl+Enter)")

            self.btn_copy.setText("Copy")
            self.btn_copy.setEnabled(False)
            return

        cursor = self.txt_result.textCursor()
        cursor.insertText(" ▍", self.result_char_fmt)

        if dictionary_source_lang is None or dictionary_target_lang is None:
            dictionary_source_lang, dictionary_target_lang = self.current_dictionary_languages()

        self.trans_thread = TranslationThread(
            text,
            mode,
            dictionary_source_lang,
            dictionary_target_lang,
        )
        self.trans_thread.chunk_received.connect(self.append_translation_chunk)
        self.trans_thread.finished.connect(self.on_translation_finished)
        self.trans_thread.start()

    # ---------- 结果输出 ----------
    def append_translation_chunk(self, chunk):
        self.full_translation += chunk
        cursor = self.txt_result.textCursor()

        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, 2)
        if cursor.selectedText() == " ▍":
            cursor.removeSelectedText()
        else:
            cursor.movePosition(QTextCursor.MoveOperation.End)

        cursor.setBlockFormat(self.result_block_fmt)
        cursor.insertText(chunk, self.result_char_fmt)
        cursor.insertText(" ▍", self.result_char_fmt)

        self.txt_result.setTextCursor(cursor)
        self.txt_result.ensureCursorVisible()

    def on_translation_finished(self, success, error_msg):
        log(f"[UI] on_translation_finished success={success}, error={error_msg}")
        cursor = self.txt_result.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, 2)
        if cursor.selectedText() == " ▍":
            cursor.removeSelectedText()

        if not success and error_msg != "已取消":
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(f"\n\n[翻译出错: {error_msg}]", self.result_char_fmt)

        if success and self.full_translation.strip():
            self.render_markdown_text(self.txt_result, self.full_translation, "result")

        if self.source_mode == "manual":
            self.btn_translate.setEnabled(True)
            self.btn_translate.setText("Translate (Ctrl+Enter)")

        self.btn_copy.setText("Copy")
        self.btn_copy.setEnabled(bool(self.full_translation.strip()))

    # ---------- 剪贴板 ----------
    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        if self.full_translation:
            clipboard.setText(self.full_translation.strip())
            self.btn_copy.setText("Copied")
            QTimer.singleShot(900, lambda: self.btn_copy.setText("Copy"))
            log("[Clipboard] copied translation")

    # ---------- 关闭行为 ----------
    def closeEvent(self, event):
        if self.force_quit:
            log("[UI] closeEvent force quit")
            self.cancel_current_translation()
            if self.xbutton1_hook:
                self.xbutton1_hook.uninstall()
            super().closeEvent(event)
        else:
            log("[UI] closeEvent hide to tray")
            event.ignore()
            self.hide()


def main():
    raw_text = ""

    if len(sys.argv) > 2 and sys.argv[1] == "--image":
        image_path = sys.argv[2]
        delete_after = "--delete-after" in sys.argv[3:]
        raw_text = build_ocr_image_request(image_path, delete_after)
    elif len(sys.argv) > 2 and sys.argv[1] == "--file":
        file_path = sys.argv[2]
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except Exception as e:
            log(f"[Main] read temp file error: {e}")
            raw_text = ""
        finally:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                log(f"[Main] remove temp file error: {e}")
    elif len(sys.argv) > 1:
        raw_text = " ".join(sys.argv[1:]).strip()

    log(f"[Main] program started, pid={os.getpid()}, raw_text={repr(raw_text[:300])}")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if raw_text:
        if send_to_existing_instance(raw_text):
            log("[Main] text sent to existing instance, exiting current process")
            return

    window = TranslationWindow()

    try:
        server = SingleInstanceServer()
        server.message_received.connect(window.handle_new_request)
        log("[Main] single instance server started")
    except Exception as e:
        server = None
        log(f"[Main] single instance server init failed: {e}")

        if raw_text and send_to_existing_instance(raw_text, retries=6, delay_ms=220):
            log("[Main] fallback send succeeded after server init failed, exiting")
            return

    if raw_text:
        QTimer.singleShot(120, lambda: window.handle_new_request(raw_text))
    else:
        # 先真实显示一次窗口，再隐藏到托盘。
        # 在 Windows + pythonw + 托盘场景下，这有助于后续窗口被可靠恢复显示。
        window.show()
        QTimer.singleShot(0, window.hide)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
