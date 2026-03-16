import sys
import os
import warnings
from urllib.parse import unquote
import google.generativeai as genai

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QTextEdit, 
                             QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout)
from PyQt6.QtGui import (QColor, QScreen, QTextCursor, QTextCharFormat, 
                         QPalette, QTextBlockFormat, QFont, QPainter, QBrush, QPen)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QPoint

warnings.filterwarnings("ignore")  # 强行屏蔽所有警告

# ================= 配置区域 =================
# ⚠️ 请填入你的 API Key
GOOGLE_API_KEY = "AIzaSyDhCx-m-xyYmY5r_5bE3VMIUWbek_-UTHA"

# 代理设置
PROXY_URL = 'http://127.0.0.1:7897'
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 

# 配置 Gemini
genai.configure(api_key=GOOGLE_API_KEY.strip(), transport='rest')
model = genai.GenerativeModel('gemma-3-27b-it')
# ===========================================

def translate_text(text):
    """调用 Gemini 进行全文翻译"""
    try:
        prompt = f"""
        你是一个翻译引擎。请将下方的文本翻译成简体中文。
        规则：
        1. 直接输出译文，不要解释，不要加前缀。
        2. 如果文本包含乱码或无法翻译，请原样保留。
        
        待翻译文本：
        {text}
        """
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"翻译出错: {str(e)}"

# ================= 1. 后台查词线程 =================
class DictionaryThread(QThread):
    """
    后台线程：用于划词查询单词或短语的释义，调用 Gemini 避免卡死主界面
    """
    result_ready = pyqtSignal(str, str) # 信号：(原文, 译文)

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        try:
            prompt = f"请简要翻译并解释以下单词或短语，直接输出中文释义，不要废话：\n{self.text}"
            response = model.generate_content(prompt)
            self.result_ready.emit(self.text, response.text.strip())
        except Exception as e:
            self.result_ready.emit(self.text, "查询失败")

# ================= 2. 自定义悬浮气泡 =================
class PopupLabel(QWidget):
    """
    美化的悬浮气泡。
    采用内外层嵌套结构：外层透明留出边距，内层绘制背景和阴影，彻底解决 UpdateLayeredWindowIndirect 报错。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        # 顶层窗口属性：提示层、无边框、背景透明
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # 1. 外层布局，留出 15px 的边距给阴影显示
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # 2. 内层实体卡片
        self.inner_frame = QFrame(self)
        self.inner_frame.setStyleSheet("""
            QFrame {
                background-color: #effdff;
                border: 1px solid #4B4D51;
                border-radius: 6px;
            }
        """)
        
        # 3. 内层文本布局
        inner_layout = QVBoxLayout(self.inner_frame)
        inner_layout.setContentsMargins(12, 8, 12, 8)
        
        self.label = QLabel(self.inner_frame)
        self.label.setStyleSheet("""
            QLabel {
                color: #2c3e50;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
                border: none;
                background: transparent;
            }
        """)
        inner_layout.addWidget(self.label)
        
        layout.addWidget(self.inner_frame)
        
        # 4. 将阴影加在内层卡片上，而不是顶层窗口
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.inner_frame.setGraphicsEffect(shadow)
        
        self.hide()

    def show_message(self, text, global_pos):
        self.label.setText(text)
        self.adjustSize()
        # 调整位置：因为外层有 15px 的 margin，所以 x 和 y 都要做相应偏移，保证鼠标箭头刚好指在卡片边缘
        self.move(global_pos.x() - 15, global_pos.y() - self.height() + 5)
        self.show()
        self.raise_()

# ================= 3. 增强版文本框 =================
class InteractiveTextEdit(QTextEdit):
    """支持：鼠标悬停段落高亮 + 自动滚动 + 划词翻译"""
    hover_index_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.segment_ranges = []    
        self.current_highlight_index = -1
        
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
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

    def set_segments(self, ranges):
        self.segment_ranges = ranges

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.NoButton:
            cursor = self.cursorForPosition(event.pos())
            pos = cursor.position()
            
            found_index = -1
            for i, (start, end) in enumerate(self.segment_ranges):
                if start <= pos < end: 
                    found_index = i
                    break
            
            if found_index != self.current_highlight_index:
                self.current_highlight_index = found_index
                self.hover_index_changed.emit(found_index)
        
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.current_highlight_index = -1
        self.hover_index_changed.emit(-1)
        super().leaveEvent(event)

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
            self.dict_thread.terminate()
            self.dict_thread.wait()

        self.dict_thread = DictionaryThread(text)
        self.dict_thread.result_ready.connect(lambda orig, trans: self.show_popup_result(orig, trans, pos))
        self.dict_thread.start()

    def show_popup_result(self, original, translation, pos):
        display_text = f"{original}\n⬇\n{translation}"
        self.popup.show_message(display_text, pos)

    def closeEvent(self, event):
        self.popup.close()
        super().closeEvent(event)

    def highlight_segment(self, index):
        if index < 0 or index >= len(self.segment_ranges):
            self.setExtraSelections([])
            return

        start, end = self.segment_ranges[index]
        if start == end: 
            self.setExtraSelections([])
            return

        selection = QTextEdit.ExtraSelection()
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        selection.cursor = cursor
        
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#FFF8C5")) 
        selection.format = fmt
        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)

        self.setExtraSelections([selection])

    def scroll_to_segment(self, index):
        if index < 0 or index >= len(self.segment_ranges):
            return
        start, _ = self.segment_ranges[index]
        cursor = self.textCursor()
        cursor.setPosition(start)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

# ================= 4. 主窗口逻辑 =================
class TranslationWindow(QWidget):
    def __init__(self, original_segments, translated_segments):
        super().__init__()
        self.original_segments = original_segments
        self.translated_segments = translated_segments
        self.initUI()
        
        self.populate_text(self.txt_origin, self.original_segments, is_translation=False)
        self.populate_text(self.txt_result, self.translated_segments, is_translation=True)
        self.copy_to_clipboard()

    def populate_text(self, text_edit: InteractiveTextEdit, segments, is_translation=False):
        text_edit.clear()
        cursor = text_edit.textCursor()
        ranges = []
        
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        block_fmt = QTextBlockFormat()
        
        if is_translation:
            font.setPixelSize(15) 
            color = QColor("#2c3e50")
            block_fmt.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            block_fmt.setBottomMargin(12) 
        else:
            font.setPixelSize(13)
            color = QColor("#606266")
            block_fmt.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            block_fmt.setBottomMargin(6)
        
        char_fmt = QTextCharFormat()
        char_fmt.setFont(font)
        char_fmt.setForeground(color)

        for segment in segments:
            start_pos = cursor.position()
            cursor.insertText(segment, char_fmt)
            cursor.setBlockFormat(block_fmt)
            end_pos = cursor.position()
            ranges.append((start_pos, end_pos))
            cursor.insertBlock()
        
        text_edit.set_segments(ranges)
        text_edit.moveCursor(QTextCursor.MoveOperation.Start)

    def sync_highlight(self, index):
        sender = self.sender()
        if sender == self.txt_origin:
            self.txt_origin.highlight_segment(index)
            if index < len(self.translated_segments):
                self.txt_result.highlight_segment(index)
                self.txt_result.scroll_to_segment(index)
            else:
                self.txt_result.highlight_segment(-1)

        elif sender == self.txt_result:
            self.txt_result.highlight_segment(index)
            if index < len(self.original_segments):
                self.txt_origin.highlight_segment(index)
                self.txt_origin.scroll_to_segment(index)
            else:
                self.txt_origin.highlight_segment(-1)

    def initUI(self):
        total_text = "".join(self.translated_segments)
        text_len = len(total_text)
        
        base_width = 500 
        if text_len < 50: base_height = 350
        elif text_len < 200: base_height = 500
        else: base_height = 700
            
        self.setWindowTitle('Gemini 翻译 (支持划词)')
        self.resize(base_width, base_height)
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
        self.txt_origin.hover_index_changed.connect(self.sync_highlight)
        card_layout.addWidget(self.txt_origin)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: transparent; border-top: 1px dashed #E0E0E0; max-height: 1px; margin: 4px 0;")
        card_layout.addWidget(line)

        lbl_result = QLabel("GEMINI TRANSLATION")
        lbl_result.setStyleSheet("color: #8E44AD; font-size: 10px; font-weight: 700; letter-spacing: 1px; margin-top: 2px;")
        card_layout.addWidget(lbl_result)

        self.txt_result = InteractiveTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setStyleSheet("background-color: transparent;")
        self.txt_result.hover_index_changed.connect(self.sync_highlight)
        card_layout.addWidget(self.txt_result)

        main_layout.addWidget(self.card_frame)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        btn_layout.addStretch(1)

        self.close_btn = QPushButton("Copy & Close") 
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #8E44AD;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-weight: 600; font-size: 13px;
            }
            QPushButton:hover { background-color: #9B59B6; }
        """)
        btn_layout.addWidget(self.close_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        full_text = "\n".join(self.translated_segments)
        clipboard.setText(full_text)

def main():
    if len(sys.argv) > 1:
        raw_text = " ".join(sys.argv[1:])
        clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
        text_to_translate = unquote(clean_text)
    else:
        print("正在进行本地测试...")
        text_to_translate = (
            "The quick brown fox jumps over the lazy dog. \n"
            "This is a sentence that spans across \n"
            "multiple lines to test if the translator can \n"
            "understand the full context correctly."
        )

    # 1. 预处理原文
    original_segments = text_to_translate.split('\n')
    
    # 2. 全文翻译 (调用 Gemini)
    res_text = translate_text(text_to_translate)
    if "翻译出错" not in res_text:
        translated_segments = res_text.split('\n')
    else:
        translated_segments = [res_text]

    # 3. 启动 UI
    app = QApplication(sys.argv)
    window = TranslationWindow(original_segments, translated_segments)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
