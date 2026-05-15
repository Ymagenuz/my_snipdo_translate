import sys
import os
import re
import warnings
import time
import ctypes
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
    QTextBlockFormat, QFont, QAction, QIcon
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject, QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
GPTSAPI_API_KEY = os.getenv("GPTSAPI_API_KEY", "").strip()
client = None

# PROXY_URL = 'http://127.0.0.1:7897'
# os.environ['HTTPS_PROXY'] = PROXY_URL
# os.environ['HTTP_PROXY'] = PROXY_URL

MODEL_NAME = 'gpt-4o-mini'
SERVER_NAME = "gptsapi_translate_snipdo_single_instance_v1"


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
        base_url="https://api.gptsapi.net/v1"
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

SW_RESTORE = 9
SW_SHOW = 5


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


def normalize_input_text(raw_text: str):
    """
    处理来自 SnipDo 的原始文本，返回段落列表
    """
    if not raw_text:
        return []

    clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
    text_to_translate = unquote(clean_text)
    text_to_translate = normalize_newlines(text_to_translate)

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
1. 保持原文的段落结构：原文有几段，译文就输出几段，段落之间用换行符隔开。
2. 追求信达雅：根据目标语言的表达习惯自由调整句式，确保译文流畅、自然、专业。
3. 直接输出译文，不要说明识别到的语言，不要添加前缀或解释。

待翻译文本：
{text_clean}
"""

            return f"""
你是一个专业的多语言翻译引擎。请先自动识别下方原文的主要语言，再按默认规则翻译：
1. 如果原文主要是中文（含简体或繁体），翻译成地道的英文。
2. 如果原文主要是英文，翻译成地道的简体中文。
3. 如果原文是其他语言，默认翻译成地道的简体中文；专有名词、代码、型号和品牌名按语境保留或自然处理。

规则：
1. 保持原文的段落结构：原文有几段，译文就输出几段，段落之间用换行符隔开。
2. 追求信达雅：根据目标语言的表达习惯自由调整句式，确保译文流畅、自然、专业。
3. 直接输出译文，不要说明识别到的语言，不要添加前缀或解释。

待翻译文本：
{text_clean}
"""

        if actual_mode == "zh2en":
            return f"""
你是一个专业的多语言翻译引擎。请将下方文本翻译成地道的英文。
原文语言设置：{source_lang}。如果是 Auto，请先自动识别原文语言。

规则：
1. 保持原文的段落结构：原文有几段，译文就输出几段，段落之间用换行符隔开。
2. 追求信达雅：根据英文母语者的表达习惯自由调整句式，确保译文流畅、自然、专业。
3. 直接输出译文，不要任何解释，不要加前缀。

待翻译文本：
{text_clean}
"""
        else:
            return f"""
你是一个专业的多语言翻译引擎。请将下方文本翻译成地道的简体中文。
原文语言设置：{source_lang}。如果是 Auto，请先自动识别原文语言。

规则：
1. 保持原文的段落结构：原文有几段，译文就输出几段，段落之间用换行符隔开。
2. 追求信达雅：根据中文表达习惯自由调整句式，确保译文流畅、自然、专业。
3. 直接输出译文，不要任何解释，不要加前缀。

待翻译文本：
{text_clean}
"""

    def run(self):
        try:
            if client is None:
                raise RuntimeError("未设置 GPTSAPI_API_KEY")

            prompt = self.build_prompt()
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


# ================= 2. 统一文本框：支持 Ctrl+Enter =================
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

        self.init_ui()
        self.setup_result_format()
        self.setup_tray_icon()
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

        tray_menu.addSeparator()

        quit_action = QAction("彻底退出", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        log(f"[Tray] activated: {reason}")
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick
        ):
            self.show_manual_window()

    def show_manual_window(self):
        log("[UI] show_manual_window called")
        self.cancel_current_translation()
        self.source_mode = "manual"
        self.apply_manual_mode_ui(reset_content=True)

        self.force_show_window()
        self.txt_origin.setFocus()

    def quit_app(self):
        log("[App] quit_app called")
        self.force_quit = True
        self.cancel_current_translation()
        self.tray_icon.hide()
        QApplication.quit()

    def cancel_current_translation(self):
        if self.trans_thread and self.trans_thread.isRunning():
            log("[UI] cancel_current_translation")
            self.trans_thread.request_stop()
            self.trans_thread.wait(800)

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

        self.lbl_dictionary_lang = QLabel("语言")
        self.lbl_dictionary_lang.setStyleSheet("color: #909399; font-size: 11px; font-weight: 600;")
        self.lbl_dictionary_lang.setToolTip("原文语言与译文/释义语言")
        header_layout.addWidget(self.lbl_dictionary_lang)

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

        self.cbo_dictionary_source = QComboBox()
        self.cbo_dictionary_source.setStyleSheet(combo_style)
        self.cbo_dictionary_source.setFixedWidth(82)
        self.cbo_dictionary_source.setToolTip("原文语言")
        for value, label in DICTIONARY_SOURCE_LANGUAGES:
            self.cbo_dictionary_source.addItem(label, value)
        self.cbo_dictionary_source.currentIndexChanged.connect(self.on_dictionary_language_changed)
        header_layout.addWidget(self.cbo_dictionary_source)

        self.lbl_dictionary_arrow = QLabel("→")
        self.lbl_dictionary_arrow.setStyleSheet("color: #C0C4CC; font-size: 12px;")
        header_layout.addWidget(self.lbl_dictionary_arrow)

        self.cbo_dictionary_target = QComboBox()
        self.cbo_dictionary_target.setStyleSheet(combo_style)
        self.cbo_dictionary_target.setFixedWidth(82)
        self.cbo_dictionary_target.setToolTip("译文/释义语言")
        for value, label in DICTIONARY_TARGET_LANGUAGES:
            self.cbo_dictionary_target.addItem(label, value)
        self.cbo_dictionary_target.currentIndexChanged.connect(self.on_dictionary_language_changed)
        header_layout.addWidget(self.cbo_dictionary_target)

        card_layout.addLayout(header_layout)

        self.txt_origin = InteractiveTextEdit()
        self.txt_origin.submit_signal.connect(self.start_manual_translation)
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

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: transparent; border-top: 1px dashed #E0E0E0; max-height: 1px; margin: 4px 0;")
        card_layout.addWidget(line)

        self.lbl_result = QLabel("AUTO TRANSLATION")
        self.lbl_result.setStyleSheet("color: #8E44AD; font-size: 10px; font-weight: 700; letter-spacing: 1px; margin-top: 2px;")
        card_layout.addWidget(self.lbl_result)

        self.txt_result = InteractiveTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setStyleSheet("background-color: transparent;")
        card_layout.addWidget(self.txt_result)

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
            log("[UI] API key configured from manual input")
            return True

        log("[UI] API key input cancelled or empty")
        return False

    def clear_manual_origin(self):
        if self.source_mode != "manual":
            return

        self.txt_origin.clear()
        self.original_paragraphs = []
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

    # ---------- 模式切换 ----------

    def apply_manual_mode_ui(self, reset_content=False):
        self.source_mode = "manual"

        self.btn_content_mode.show()
        self.btn_clear_origin.show()
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

        self.btn_content_mode.show()
        self.update_content_mode_button_text()
        self.btn_translate.hide()
        self.btn_clear_origin.hide()
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
            self.cancel_current_translation()

            self.original_paragraphs = normalize_input_text(raw_text)
            log(f"[UI] normalized paragraphs count={len(self.original_paragraphs)}")

            if not self.original_paragraphs:
                log("[UI] no valid paragraphs after normalize_input_text")
                return

            total_text = "\n".join(self.original_paragraphs).strip()
            self.pending_snipdo_text = total_text

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
            super().closeEvent(event)
        else:
            log("[UI] closeEvent hide to tray")
            event.ignore()
            self.hide()


def main():
    raw_text = ""

    if len(sys.argv) > 2 and sys.argv[1] == "--file":
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
