import sys
import os
import re
import warnings
import time
import ctypes
from urllib.parse import unquote

from google import genai

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTextEdit,
    QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QSystemTrayIcon, QMenu
)
from PyQt6.QtGui import (
    QColor, QScreen, QTextCursor, QTextCharFormat,
    QTextBlockFormat, QFont, QAction, QIcon
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject, QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
if not GOOGLE_API_KEY:
    raise RuntimeError("未设置 GOOGLE_API_KEY 环境变量")

PROXY_URL = 'http://127.0.0.1:7897'
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

MODEL_NAME = 'gemma-3-27b-it'
SERVER_NAME = "gemini_translate_snipdo_single_instance_v3"

client = genai.Client(api_key=GOOGLE_API_KEY)
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
    text_clean = text.strip()
    words = text_clean.split()
    return len(words) <= 5 and len(text_clean) < 50


def contains_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def detect_translation_mode(text: str) -> str:
    """
    自动判断翻译方向：
    - 含中文 -> 中译英
    - 否则 -> 英译中
    """
    if contains_chinese(text):
        return "zh2en"
    return "en2zh"


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

    def __init__(self, text: str, mode: str = "auto"):
        super().__init__()
        self.text = text
        self.mode = mode  # auto / en2zh / zh2en / dictionary
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def build_prompt(self) -> str:
        text_clean = self.text.strip()

        if self.mode == "dictionary":
            return f"""
请对下面的单词或短语进行详细释义，不要使用markdown语法。
请按以下结构输出：
1. 音标：音标（如果是英文）
2. 释义：词性及对应的中文释义（如果有多个常用释义，请列出）
3. 例句：提供 1-2 个简短且实用的双语例句

待查内容：
{text_clean}
"""

        actual_mode = self.mode
        if actual_mode == "auto":
            actual_mode = detect_translation_mode(text_clean)

        if actual_mode == "zh2en":
            return f"""
你是一个专业的翻译引擎。请将下方的中文文本翻译成地道的英文。
规则：
1. 保持原文的段落结构：原文有几段，译文就输出几段，段落之间用换行符隔开。
2. 追求信达雅：根据英文母语者的表达习惯自由调整句式，确保译文流畅、自然、专业。
3. 直接输出译文，不要任何解释，不要加前缀。

待翻译文本：
{text_clean}
"""
        else:
            return f"""
你是一个专业的翻译引擎。请将下方的英文文本翻译成地道的简体中文。
规则：
1. 保持原文的段落结构：原文有几段，译文就输出几段，段落之间用换行符隔开。
2. 追求信达雅：根据中文表达习惯自由调整句式，确保译文流畅、自然、专业。
3. 直接输出译文，不要任何解释，不要加前缀。

待翻译文本：
{text_clean}
"""

    def run(self):
        try:
            prompt = self.build_prompt()
            log(f"[TranslateThread] start, mode={self.mode}, text={repr(self.text[:200])}")

            response = client.models.generate_content_stream(
                model=MODEL_NAME,
                contents=prompt
            )

            for chunk in response:
                if self._stop_requested:
                    log("[TranslateThread] cancelled")
                    self.finished.emit(False, "已取消")
                    return

                try:
                    if getattr(chunk, "text", None):
                        self.chunk_received.emit(chunk.text)
                except Exception:
                    continue

            log("[TranslateThread] finished success")
            self.finished.emit(True, "")
        except Exception as e:
            log(f"[TranslateThread] error: {e}")
            self.finished.emit(False, str(e))


# ================= 2. 后台悬浮查词线程 =================
class DictionaryThread(QThread):
    result_ready = pyqtSignal(str, str)

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        try:
            prompt = f"""
你是一个极简词典。请对以下单词或短语提供简明释义。
严格按照以下两行格式输出，不要任何 Markdown 符号，不要多余解释：
[音标]
[词性] 中文释义1；中文释义2

待查内容：
{self.text}
"""
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt
            )
            result_text = (response.text or "").strip() if response else "查询失败"
            self.result_ready.emit(self.text, result_text)
        except Exception as e:
            log(f"[DictionaryThread] error: {e}")
            self.result_ready.emit(self.text, "查询失败")


# ================= 3. 自定义悬浮气泡 =================
class PopupLabel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        self.inner_frame = QFrame(self)
        self.inner_frame.setStyleSheet("""
            QFrame { background-color: #effdff; border: 1px solid #4B4D51; border-radius: 6px; }
        """)

        inner_layout = QVBoxLayout(self.inner_frame)
        inner_layout.setContentsMargins(12, 8, 12, 8)

        self.label = QLabel(self.inner_frame)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(
            "color: #2c3e50; font-family: 'Segoe UI', 'Microsoft YaHei UI'; "
            "font-size: 13px; border: none; background: transparent;"
        )
        inner_layout.addWidget(self.label)
        layout.addWidget(self.inner_frame)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.inner_frame.setGraphicsEffect(shadow)
        self.hide()

    def show_message(self, text, global_pos):
        self.label.setText(text)
        self.adjustSize()
        self.move(global_pos.x() - 15, global_pos.y() - self.height() + 5)
        self.show()
        self.raise_()


# ================= 4. 统一文本框：支持选词查词 + Ctrl+Enter =================
class InteractiveTextEdit(QTextEdit):
    submit_signal = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.popup = PopupLabel()
        self.dict_thread = None
        self.lookup_enabled = True

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

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)

        if not self.lookup_enabled:
            return

        cursor = self.textCursor()
        selected_text = cursor.selectedText().strip()

        if selected_text and len(selected_text) < 80:
            rect = self.cursorRect(cursor)
            global_pos = self.mapToGlobal(rect.topLeft())
            self.start_lookup(selected_text, global_pos)
        else:
            self.popup.hide()

    def start_lookup(self, text, pos):
        if self.dict_thread and self.dict_thread.isRunning():
            self.dict_thread.quit()
            self.dict_thread.wait(200)

        self.dict_thread = DictionaryThread(text)
        self.dict_thread.result_ready.connect(
            lambda orig, trans: self.show_popup_result(orig, trans, pos)
        )
        self.dict_thread.start()

    def show_popup_result(self, original, translation, pos):
        self.popup.show_message(f"{original}\n⬇\n{translation}", pos)

    def closeEvent(self, event):
        self.popup.close()
        super().closeEvent(event)


# ================= 5. 单实例本地通信服务 =================
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


# ================= 6. 主窗口逻辑 =================
class TranslationWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.source_mode = "manual"         # manual / snipdo
        self.translation_mode = "zh2en"     # manual mode: auto / en2zh / zh2en / dictionary
        self.snipdo_translation_override = "auto"   # auto / en2zh / zh2en
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
        self.tray_icon.setToolTip("Gemini 翻译（统一版）")

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

        self.lbl_origin = QLabel("CHINESE (ORIGINAL)")
        self.lbl_origin.setStyleSheet("color: #909399; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        header_layout.addWidget(self.lbl_origin)

        header_layout.addStretch()

        self.btn_toggle = QPushButton("🔄 切换为英译中")
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #409EFF;
                border: none;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #66B1FF;
            }
        """)
        self.btn_toggle.clicked.connect(self.toggle_mode)
        header_layout.addWidget(self.btn_toggle)

        self.btn_snipdo_mode = QPushButton("Auto")
        self.btn_snipdo_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_snipdo_mode.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #67C23A;
                border: none;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #85CE61;
            }
        """)
        self.btn_snipdo_mode.clicked.connect(self.toggle_snipdo_translation_mode)
        self.btn_snipdo_mode.hide()
        header_layout.addWidget(self.btn_snipdo_mode)

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

        self.lbl_result = QLabel("ENGLISH TRANSLATION")
        self.lbl_result.setStyleSheet("color: #8E44AD; font-size: 10px; font-weight: 700; letter-spacing: 1px; margin-top: 2px;")
        card_layout.addWidget(self.lbl_result)

        self.txt_result = InteractiveTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.lookup_enabled = True
        self.txt_result.setStyleSheet("background-color: transparent;")
        card_layout.addWidget(self.txt_result)

        main_layout.addWidget(self.card_frame)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 10, 0, 0)
        btn_layout.setSpacing(10)
        btn_layout.addStretch(1)

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

        self.btn_copy_hide = QPushButton("Copy & Hide")
        self.btn_copy_hide.setEnabled(False)
        self.btn_copy_hide.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_hide.clicked.connect(self.copy_and_hide)
        self.btn_copy_hide.setStyleSheet("""
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
        btn_layout.addWidget(self.btn_copy_hide)

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

    # ---------- 模式切换 ----------
    def update_snipdo_mode_button_text(self):
        if self.snipdo_translation_override == "auto":
            self.btn_snipdo_mode.setText("Auto")
        elif self.snipdo_translation_override == "en2zh":
            self.btn_snipdo_mode.setText("英译中")
        else:
            self.btn_snipdo_mode.setText("中译英")

    def apply_manual_mode_ui(self, reset_content=False):
        self.source_mode = "manual"

        self.btn_toggle.show()
        self.btn_snipdo_mode.hide()
        self.btn_translate.show()
        self.txt_origin.setReadOnly(False)
        self.txt_origin.lookup_enabled = False
        self.txt_origin.setMaximumHeight(220)

        if self.translation_mode not in ("zh2en", "en2zh"):
            self.translation_mode = "zh2en"

        if self.translation_mode == "zh2en":
            self.lbl_origin.setText("CHINESE (ORIGINAL)")
            self.lbl_result.setText("ENGLISH TRANSLATION")
            self.btn_toggle.setText("🔄 切换为英译中")
            self.txt_origin.setPlaceholderText("在此输入或粘贴需要翻译的中文...\n按 Ctrl + Enter 开始翻译")
        else:
            self.lbl_origin.setText("ENGLISH (ORIGINAL)")
            self.lbl_result.setText("CHINESE TRANSLATION")
            self.btn_toggle.setText("🔄 切换为中译英")
            self.txt_origin.setPlaceholderText("在此输入或粘贴需要翻译的英文...\n按 Ctrl + Enter 开始翻译")

        if reset_content:
            self.original_paragraphs = []
            self.full_translation = ""
            self.txt_origin.clear()
            self.setup_result_format()
            self.btn_translate.setEnabled(True)
            self.btn_translate.setText("Translate (Ctrl+Enter)")
            self.btn_copy_hide.setEnabled(False)

    def apply_snipdo_mode_ui(self):
        self.source_mode = "snipdo"

        self.btn_toggle.hide()
        self.btn_translate.hide()
        self.txt_origin.setReadOnly(True)
        self.txt_origin.lookup_enabled = True
        self.txt_origin.setPlaceholderText("")

        total_text = "\n".join(self.original_paragraphs).strip()
        dict_mode = is_dictionary_mode(total_text)

        if dict_mode:
            self.btn_snipdo_mode.hide()
            self.lbl_origin.setText("ORIGINAL")
            self.lbl_result.setText("DICTIONARY")
        else:
            self.btn_snipdo_mode.show()
            self.update_snipdo_mode_button_text()

            effective_mode = self.snipdo_translation_override
            if effective_mode == "auto":
                effective_mode = detect_translation_mode(total_text)

            if effective_mode == "zh2en":
                self.lbl_origin.setText("CHINESE (ORIGINAL)")
                self.lbl_result.setText("ENGLISH TRANSLATION")
            else:
                self.lbl_origin.setText("ENGLISH (ORIGINAL)")
                self.lbl_result.setText("CHINESE TRANSLATION")

    def toggle_mode(self):
        if self.source_mode != "manual":
            return

        self.translation_mode = "en2zh" if self.translation_mode == "zh2en" else "zh2en"
        self.apply_manual_mode_ui(reset_content=True)
        self.txt_origin.setFocus()

    def toggle_snipdo_translation_mode(self):
        if self.source_mode != "snipdo":
            return

        order = ["auto", "en2zh", "zh2en"]
        try:
            idx = order.index(self.snipdo_translation_override)
        except ValueError:
            idx = 0

        self.snipdo_translation_override = order[(idx + 1) % len(order)]
        self.update_snipdo_mode_button_text()

        total_text = self.pending_snipdo_text.strip() or "\n".join(self.original_paragraphs).strip()
        if not total_text:
            return

        if is_dictionary_mode(total_text):
            self.apply_snipdo_mode_ui()
            return

        self.cancel_current_translation()
        self.full_translation = ""
        self.setup_result_format()
        self.btn_copy_hide.setText("Translating...")
        self.btn_copy_hide.setEnabled(False)

        self.apply_snipdo_mode_ui()
        self.start_translation(total_text, self.snipdo_translation_override)

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
        dict_mode = is_dictionary_mode(total_text)

        base_width = 560

        if self.source_mode == "manual":
            base_height = 680
        else:
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

            self.full_translation = ""
            self.btn_copy_hide.setText("Translating...")
            self.btn_copy_hide.setEnabled(False)

            total_text = "\n".join(self.original_paragraphs).strip()
            self.pending_snipdo_text = total_text

            if is_dictionary_mode(total_text):
                effective_mode = "dictionary"
            else:
                effective_mode = (
                    self.snipdo_translation_override
                    if self.snipdo_translation_override in ("en2zh", "zh2en")
                    else "auto"
                )

            self.translation_mode = effective_mode
            log(f"[UI] translation_mode={self.translation_mode}, snipdo_override={self.snipdo_translation_override}, total_text={repr(total_text[:300])}")

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

        self.cancel_current_translation()

        self.original_paragraphs = [p.strip() for p in re.split(r'\n+', normalize_newlines(text_to_translate)) if p.strip()]
        self.full_translation = ""
        self.setup_result_format()

        self.btn_translate.setEnabled(False)
        self.btn_translate.setText("Translating...")
        self.btn_copy_hide.setEnabled(False)
        self.btn_copy_hide.setText("Copy & Hide")

        self.start_translation(text_to_translate, self.translation_mode)

    def start_translation(self, text: str, mode: str):
        log(f"[UI] start_translation, mode={mode}, text={repr(text[:300])}")
        cursor = self.txt_result.textCursor()
        cursor.insertText(" ▍", self.result_char_fmt)

        self.trans_thread = TranslationThread(text, mode)
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

        if success and self.source_mode == "snipdo":
            self.copy_to_clipboard()

        if self.source_mode == "manual":
            self.btn_translate.setEnabled(True)
            self.btn_translate.setText("Translate (Ctrl+Enter)")

        self.btn_copy_hide.setText("Copy & Hide")
        self.btn_copy_hide.setEnabled(True)

    # ---------- 剪贴板 ----------
    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        if self.full_translation:
            clipboard.setText(self.full_translation.strip())
            log("[Clipboard] copied translation")

    def copy_and_hide(self):
        self.copy_to_clipboard()
        self.hide()

    # ---------- 关闭行为 ----------
    def closeEvent(self, event):
        if self.force_quit:
            log("[UI] closeEvent force quit")
            self.cancel_current_translation()
            self.txt_origin.popup.close()
            self.txt_result.popup.close()
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
