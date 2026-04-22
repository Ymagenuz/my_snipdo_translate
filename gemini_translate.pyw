import sys
import os
import re
import warnings
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
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
# 建议优先使用环境变量
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
if not GOOGLE_API_KEY:
    # 兼容旧方式；正式使用时建议删除这一行 fallback
    GOOGLE_API_KEY = "AIzaSyB1TLMbTWPAxust0rCcqcWPOGJjvtIYlmg".strip()

PROXY_URL = 'http://127.0.0.1:7897'
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

MODEL_NAME = 'gemma-3-27b-it'
SERVER_NAME = "gemini_translate_snipdo_single_instance_v1"

client = genai.Client(api_key=GOOGLE_API_KEY)
# ===========================================


# ================= 工具函数 =================
def normalize_input_text(raw_text: str):
    """
    处理来自 Snipdo 的原始文本，返回段落列表
    """
    if not raw_text:
        return []

    clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
    text_to_translate = unquote(clean_text)

    # 完美段落处理：保留段落，修复断词断行
    text = re.sub(r'-\s*\n\s*', '', text_to_translate)
    text = re.sub(r'(?<![.!?。！？>”"])\
(?!\n)', ' ', text)
    original_paragraphs = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
    return original_paragraphs


def is_dictionary_mode(text: str):
    text_clean = text.strip()
    words = text_clean.split()
    return len(words) <= 5 and len(text_clean) < 50


def send_to_existing_instance(text: str) -> bool:
    """
    尝试把文本发送给已运行的实例
    """
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)

    if not socket.waitForConnected(250):
        return False

    data = text.encode("utf-8")
    socket.write(data)
    socket.flush()
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


# ================= 1. 后台流式翻译/查词线程 =================
class TranslationThread(QThread):
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, error_message

    def __init__(self, text):
        super().__init__()
        self.text = text
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            text_clean = self.text.strip()
            dictionary_mode = is_dictionary_mode(text_clean)

            if dictionary_mode:
                prompt = f"""
请对下面的单词或短语进行详细释义，不要使用markdown语法。
请按以下结构输出：
1. 音标：音标（如果是英文）
2. 释义：词性及对应的中文释义（如果有多个常用释义，请列出）
3. 例句：提供 1-2 个简短且实用的双语例句

待查内容：
{text_clean}
"""
            else:
                prompt = f"""
你是一个专业的翻译引擎。请将下方的文本翻译成简体中文。
规则：
1. 保持原文的段落结构：原文有几段，译文就输出几段，段落之间用换行符隔开。
2. 追求信达雅：根据中文表达习惯自由调整句式，确保译文流畅自然。
3. 直接输出译文，不要任何解释，不要加前缀。

待翻译文本：
{self.text}
"""

            response = client.models.generate_content_stream(
                model=MODEL_NAME,
                contents=prompt
            )

            for chunk in response:
                if self._stop_requested:
                    self.finished.emit(False, "已取消")
                    return

                try:
                    if getattr(chunk, "text", None):
                        self.chunk_received.emit(chunk.text)
                except Exception:
                    continue

            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


# ================= 2. 后台查词线程 =================
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
        except Exception:
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


# ================= 4. 纯净版文本框 =================
class InteractiveTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.popup = PopupLabel()
        self.dict_thread = None

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

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
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

        # 防止上次异常退出遗留 server 名称
        QLocalServer.removeServer(SERVER_NAME)

        if not self.server.listen(SERVER_NAME):
            raise RuntimeError(f"无法启动本地服务: {self.server.errorString()}")

        self.server.newConnection.connect(self.handle_new_connection)

    def handle_new_connection(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self.read_socket_data(s))
            socket.disconnected.connect(socket.deleteLater)

    def read_socket_data(self, socket):
        try:
            data = bytes(socket.readAll()).decode("utf-8", errors="ignore")
            if data.strip():
                self.message_received.emit(data.strip())
        except Exception:
            pass
        finally:
            socket.disconnectFromServer()


# ================= 6. 主窗口逻辑 =================
class TranslationWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.original_paragraphs = []
        self.full_translation = ""
        self.force_quit = False
        self.trans_thread = None

        self.initUI()
        self.setup_result_format()
        self.setup_tray_icon()

    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)

        current_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(current_dir, "snipdo_script_logo", "gemini-color.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)

        self.tray_icon.setIcon(icon)
        self.setWindowIcon(icon)
        self.tray_icon.setToolTip("Gemini SnipDo 翻译")

        tray_menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        quit_action = QAction("彻底退出", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick
        ):
            self.show_window()

    def show_window(self):
        self.show()
        self.activateWindow()
        self.raise_()

    def quit_app(self):
        self.force_quit = True
        self.cancel_current_translation()
        self.tray_icon.hide()
        QApplication.quit()

    def cancel_current_translation(self):
        if self.trans_thread and self.trans_thread.isRunning():
            self.trans_thread.request_stop()
            self.trans_thread.wait(800)

    def initUI(self):
        self.setWindowTitle('Gemini 翻译（Snipdo 常驻版）')
        self.resize(500, 650)
        self.setStyleSheet("background-color: #F5F7FA;")

        center_point = QScreen.availableGeometry(QApplication.primaryScreen()).center()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())

        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

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
        lbl_origin = QLabel("ORIGINAL")
        lbl_origin.setStyleSheet("color: #909399; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        header_layout.addWidget(lbl_origin)
        header_layout.addStretch()
        card_layout.addLayout(header_layout)

        self.txt_origin = InteractiveTextEdit()
        self.txt_origin.setReadOnly(True)
        self.txt_origin.setMaximumHeight(120)
        self.txt_origin.setStyleSheet("background-color: #FAFAFA; border-left: 2px solid #E4E7ED; padding-left: 4px;")
        card_layout.addWidget(self.txt_origin)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: transparent; border-top: 1px dashed #E0E0E0; max-height: 1px; margin: 4px 0;")
        card_layout.addWidget(line)

        self.lbl_result = QLabel("GEMINI TRANSLATION")
        self.lbl_result.setStyleSheet("color: #8E44AD; font-size: 10px; font-weight: 700; letter-spacing: 1px; margin-top: 2px;")
        card_layout.addWidget(self.lbl_result)

        self.txt_result = InteractiveTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setStyleSheet("background-color: transparent;")
        card_layout.addWidget(self.txt_result)

        main_layout.addWidget(self.card_frame)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        btn_layout.addStretch(1)

        self.close_btn = QPushButton("Copy & Hide")
        self.close_btn.setEnabled(False)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.copy_and_hide)
        self.close_btn.setStyleSheet("""
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
        btn_layout.addWidget(self.close_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

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

    def populate_original_text(self):
        self.txt_origin.clear()
        cursor = self.txt_origin.textCursor()

        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(13)

        block_fmt = QTextBlockFormat()
        block_fmt.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        block_fmt.setBottomMargin(8)

        char_fmt = QTextCharFormat()
        char_fmt.setFont(font)
        char_fmt.setForeground(QColor("#606266"))

        for para in self.original_paragraphs:
            cursor.insertText(para, char_fmt)
            cursor.setBlockFormat(block_fmt)
            cursor.insertBlock()

        self.txt_origin.moveCursor(QTextCursor.MoveOperation.Start)

    def adjust_window_height(self):
        total_text = "\n".join(self.original_paragraphs).strip()
        text_len = len(total_text)
        dict_mode = is_dictionary_mode(total_text)

        base_width = 500

        if dict_mode:
            base_height = 700
        elif text_len < 50:
            base_height = 350
        elif text_len < 200:
            base_height = 500
        else:
            base_height = 700

        self.resize(base_width, base_height)

    def handle_new_request(self, raw_text):
        self.cancel_current_translation()

        self.original_paragraphs = normalize_input_text(raw_text)
        if not self.original_paragraphs:
            return

        self.full_translation = ""
        self.close_btn.setText("Translating...")
        self.close_btn.setEnabled(False)

        self.adjust_window_height()
        self.populate_original_text()
        self.setup_result_format()

        self.show()
        self.activateWindow()
        self.raise_()

        self.start_translation()

    def start_translation(self):
        cursor = self.txt_result.textCursor()
        cursor.insertText(" ▍", self.result_char_fmt)

        final_text_for_ai = '\n'.join(self.original_paragraphs)
        self.trans_thread = TranslationThread(final_text_for_ai)
        self.trans_thread.chunk_received.connect(self.append_translation_chunk)
        self.trans_thread.finished.connect(self.on_translation_finished)
        self.trans_thread.start()

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
        cursor = self.txt_result.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, 2)
        if cursor.selectedText() == " ▍":
            cursor.removeSelectedText()

        if not success and error_msg != "已取消":
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(f"\n\n[翻译出错: {error_msg}]", self.result_char_fmt)

        if success:
            self.copy_to_clipboard()

        self.close_btn.setText("Copy & Hide")
        self.close_btn.setEnabled(True)

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        if self.full_translation:
            clipboard.setText(self.full_translation.strip())

    def copy_and_hide(self):
        self.copy_to_clipboard()
        self.hide()

    def closeEvent(self, event):
        if self.force_quit:
            self.cancel_current_translation()
            self.txt_origin.popup.close()
            self.txt_result.popup.close()
            super().closeEvent(event)
        else:
            event.ignore()
            self.hide()


def main():
    raw_text = ""

    if len(sys.argv) > 2 and sys.argv[1] == "--file":
        file_path = sys.argv[2]
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except Exception:
            raw_text = ""
        finally:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
    elif len(sys.argv) > 1:
        raw_text = " ".join(sys.argv[1:]).strip()

    if raw_text and send_to_existing_instance(raw_text):
        return

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = TranslationWindow()

    try:
        server = SingleInstanceServer()
        server.message_received.connect(window.handle_new_request)
    except Exception:
        server = None

    if raw_text:
        window.handle_new_request(raw_text)
    else:
        window.hide()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
