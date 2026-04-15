import sys
import os
import warnings
from google import genai 

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QTextEdit, 
                             QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QSystemTrayIcon, QMenu, QStyle)
from PyQt6.QtGui import (QColor, QScreen, QTextCursor, QTextCharFormat, 
                         QTextBlockFormat, QFont, QAction, QIcon)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
GOOGLE_API_KEY = "AIzaSyB1TLMbTWPAxust0rCcqcWPOGJjvtIYlmg"

PROXY_URL = 'http://127.0.0.1:7897'
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 

client = genai.Client(api_key=GOOGLE_API_KEY.strip())
MODEL_NAME = 'gemma-3-27b-it'
# ===========================================

# ================= 1. 后台流式翻译线程 =================
class TranslationThread(QThread):
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        try:
            prompt = f"""
            你是一个专业的翻译引擎。请将下方的中文文本翻译成英文。
            规则：
            1. 保持原文的段落结构：原文有几段，译文就输出几段，段落之间用换行符隔开。
            2. 追求信达雅与专业：根据英文母语者的表达习惯自由调整句式，确保译文流畅、自然、专业。
            3. 直接输出译文，不要任何解释，不要加前缀。
            
            待翻译文本：
            {self.text}
            """

            response = client.models.generate_content_stream(
                model=MODEL_NAME,
                contents=prompt
            )
            
            for chunk in response:
                try:
                    if chunk.text:
                        self.chunk_received.emit(chunk.text)
                except Exception:
                    continue
                    
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))

# ================= 2. 支持 Ctrl+Enter 的输入框 =================
class InputTextEdit(QTextEdit):
    submit_signal = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QTextEdit { border: none; background-color: transparent; selection-background-color: #B3D8FF; selection-color: #303133; }
            QScrollBar:vertical { border: none; background: #F0F0F0; width: 6px; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #C0C4CC; min-height: 20px; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #909399; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)

    def keyPressEvent(self, event):
        if (event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter) and \
           (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.submit_signal.emit()
        else:
            super().keyPressEvent(event)

# ================= 3. 主窗口逻辑 =================
class TranslationWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.full_translation = ""
        self.force_quit = False  # 标记是否真正退出程序
        self.initUI()
        self.setup_result_format()
        self.setup_tray_icon()   # 初始化托盘

    def setup_tray_icon(self):
        """配置系统托盘图标和右键菜单"""
        self.tray_icon = QSystemTrayIcon(self)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(current_dir, "snipdo_script_logo/gemini-color.png")
        icon = QIcon(icon_path)
        self.tray_icon.setIcon(icon)
        self.setWindowIcon(icon)
        self.tray_icon.setToolTip("Gemini 中译英")

        # 创建右键菜单
        tray_menu = QMenu()
        
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)
        
        tray_menu.addSeparator()
        
        quit_action = QAction("彻底退出", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        # 绑定左键点击事件
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        """处理托盘图标的点击事件"""
        # 如果是左键单击或双击，显示窗口
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self):
        """唤出窗口并重置状态"""
        self.txt_origin.clear()
        self.txt_result.clear()
        self.full_translation = ""
        self.btn_copy_close.setEnabled(False)
        self.btn_translate.setEnabled(True)
        self.btn_translate.setText("Translate (Ctrl+Enter)")
        
        self.show()
        self.activateWindow()
        self.raise_()
        self.txt_origin.setFocus() # 自动聚焦到输入框，方便直接打字

    def quit_app(self):
        """真正的退出程序逻辑"""
        self.force_quit = True
        if hasattr(self, 'trans_thread') and self.trans_thread.isRunning():
            self.trans_thread.terminate()
            self.trans_thread.wait()
        QApplication.quit()

    def setup_result_format(self):
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(15)
        
        self.result_char_fmt = QTextCharFormat()
        self.result_char_fmt.setFont(font)
        self.result_char_fmt.setForeground(QColor("#2c3e50"))

        self.result_block_fmt = QTextBlockFormat()
        self.result_block_fmt.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        self.result_block_fmt.setBottomMargin(12)

    def start_translation(self):
        text_to_translate = self.txt_origin.toPlainText().strip()
        if not text_to_translate:
            return

        self.full_translation = ""
        self.txt_result.clear()
        self.btn_translate.setEnabled(False)
        self.btn_translate.setText("Translating...")
        self.btn_copy_close.setEnabled(False)

        cursor = self.txt_result.textCursor()
        cursor.insertText(" ▍", self.result_char_fmt)

        self.trans_thread = TranslationThread(text_to_translate)
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

        if not success:
            self.append_translation_chunk(f"\n\n[翻译出错: {error_msg}]")
        
        self.btn_translate.setEnabled(True)
        self.btn_translate.setText("Translate (Ctrl+Enter)")
        self.btn_copy_close.setEnabled(True)

    def initUI(self):
        self.setWindowTitle('Gemini 中译英工具')
        self.resize(550, 650)
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
        self.card_frame.setStyleSheet("QFrame { background-color: #FFFFFF; border-radius: 12px; border: 1px solid #EAEAEA; }")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setYOffset(4)
        shadow.setColor(QColor(0, 0, 0, 12))
        self.card_frame.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card_frame)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)

        lbl_origin = QLabel("CHINESE (ORIGINAL)")
        lbl_origin.setStyleSheet("color: #909399; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        card_layout.addWidget(lbl_origin)

        self.txt_origin = InputTextEdit()
        self.txt_origin.setPlaceholderText("在此输入或粘贴需要翻译的中文...\n按 Ctrl + Enter 开始翻译")
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(14)
        self.txt_origin.setFont(font)
        self.txt_origin.setStyleSheet("background-color: #FAFAFA; border-left: 2px solid #E4E7ED; padding-left: 6px; color: #303133;")
        self.txt_origin.submit_signal.connect(self.start_translation)
        card_layout.addWidget(self.txt_origin)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: transparent; border-top: 1px dashed #E0E0E0; max-height: 1px; margin: 4px 0;")
        card_layout.addWidget(line)

        lbl_result = QLabel("ENGLISH TRANSLATION")
        lbl_result.setStyleSheet("color: #8E44AD; font-size: 10px; font-weight: 700; letter-spacing: 1px; margin-top: 2px;")
        card_layout.addWidget(lbl_result)

        self.txt_result = QTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setStyleSheet("""
            QTextEdit { border: none; background-color: transparent; selection-background-color: #B3D8FF; selection-color: #303133; }
            QScrollBar:vertical { border: none; background: #F0F0F0; width: 6px; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #C0C4CC; min-height: 20px; border-radius: 3px; }
        """)
        card_layout.addWidget(self.txt_result)

        main_layout.addWidget(self.card_frame)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 12, 0, 0)
        btn_layout.setSpacing(10)
        btn_layout.addStretch(1)

        self.btn_translate = QPushButton("Translate (Ctrl+Enter)")
        self.btn_translate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_translate.clicked.connect(self.start_translation)
        self.btn_translate.setStyleSheet("""
            QPushButton { background-color: #F2F3F5; color: #606266; border: 1px solid #DCDFE6; border-radius: 6px; padding: 6px 16px; font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-weight: 600; font-size: 13px; }
            QPushButton:hover { background-color: #E4E7ED; color: #303133; }
            QPushButton:disabled { background-color: #F2F3F5; color: #C0C4CC; }
        """)
        btn_layout.addWidget(self.btn_translate)

        self.btn_copy_close = QPushButton("Copy & Close") 
        self.btn_copy_close.setEnabled(False)
        self.btn_copy_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_close.clicked.connect(self.copy_and_hide)
        self.btn_copy_close.setStyleSheet("""
            QPushButton { background-color: #8E44AD; color: white; border: none; border-radius: 6px; padding: 6px 16px; font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-weight: 600; font-size: 13px; }
            QPushButton:hover { background-color: #9B59B6; }
            QPushButton:disabled { background-color: #C39BD3; }
        """)
        btn_layout.addWidget(self.btn_copy_close)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    def closeEvent(self, event):
        """拦截关闭事件，改为隐藏窗口"""
        if self.force_quit:
            # 如果是点击托盘的“彻底退出”，则正常销毁
            super().closeEvent(event)
        else:
            # 否则只是隐藏窗口，保持后台运行
            event.ignore()
            self.hide()

    def copy_and_hide(self):
        """复制译文并隐藏窗口"""
        clipboard = QApplication.clipboard()
        if self.full_translation:
            clipboard.setText(self.full_translation.strip())
        self.hide()

def main():
    app = QApplication(sys.argv)
    # 关键设置：关闭最后一个窗口时不退出程序
    app.setQuitOnLastWindowClosed(False) 
    
    window = TranslationWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
