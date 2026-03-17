import sys
import os
import re
import warnings
from urllib.parse import unquote
import google.generativeai as genai

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QTextEdit, 
                             QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout)
from PyQt6.QtGui import (QColor, QScreen, QTextCursor, QTextCharFormat, 
                         QPalette, QTextBlockFormat, QFont)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
GOOGLE_API_KEY = "AIzaSyB1TLMbTWPAxust0rCcqcWPOGJjvtIYlmg"

PROXY_URL = 'http://127.0.0.1:7897'
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 

genai.configure(api_key=GOOGLE_API_KEY.strip(), transport='rest')
model = genai.GenerativeModel('gemma-3-27b-it')
# ===========================================

def translate_text(text):
    """调用 Gemini 进行段落级全文翻译，追求最高质量"""
    try:
        prompt = f"""
        你是一个专业的翻译引擎。请将下方的文本翻译成简体中文。
        规则：
        1. 保持原文的段落结构：原文有几段，译文就输出几段，段落之间用换行符隔开。
        2. 追求信达雅：根据中文表达习惯自由调整句式，确保译文流畅自然。
        3. 直接输出译文，不要任何解释，不要加前缀。
        
        待翻译文本：
        {text}
        """
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"翻译出错: {str(e)}"

# ================= 1. 后台查词线程 =================
class DictionaryThread(QThread):
    result_ready = pyqtSignal(str, str)

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
        self.label.setStyleSheet("color: #2c3e50; font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-size: 13px; border: none; background: transparent;")
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

# ================= 3. 增强版文本框 (按段落高亮) =================
class InteractiveTextEdit(QTextEdit):
    hover_index_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.segment_ranges = []    
        self.current_highlight_index = -1
        self.popup = PopupLabel() 
        self.dict_thread = None
        
        self.setStyleSheet("""
            QTextEdit { border: none; background-color: transparent; selection-background-color: #B3D8FF; selection-color: #303133; }
            QScrollBar:vertical { border: none; background: #F0F0F0; width: 6px; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #C0C4CC; min-height: 20px; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #909399; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
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
        self.popup.show_message(f"{original}\n⬇\n{translation}", pos)

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
    def __init__(self, original_paragraphs, translated_paragraphs):
        super().__init__()
        self.original_paragraphs = original_paragraphs
        self.translated_paragraphs = translated_paragraphs
        self.initUI()
        
        self.populate_text(self.txt_origin, self.original_paragraphs, is_translation=False)
        self.populate_text(self.txt_result, self.translated_paragraphs, is_translation=True)
        self.copy_to_clipboard()

    def populate_text(self, text_edit: InteractiveTextEdit, paragraphs, is_translation=False):
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
            block_fmt.setBottomMargin(8)
        
        char_fmt = QTextCharFormat()
        char_fmt.setFont(font)
        char_fmt.setForeground(color)

        for para in paragraphs:
            start_pos = cursor.position()
            cursor.insertText(para, char_fmt)
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
            if index < len(self.translated_paragraphs):
                self.txt_result.highlight_segment(index)
                self.txt_result.scroll_to_segment(index)
            else:
                self.txt_result.highlight_segment(-1)

        elif sender == self.txt_result:
            self.txt_result.highlight_segment(index)
            if index < len(self.original_paragraphs):
                self.txt_origin.highlight_segment(index)
                self.txt_origin.scroll_to_segment(index)
            else:
                self.txt_origin.highlight_segment(-1)

    def initUI(self):
        total_text = "".join(self.translated_paragraphs)
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
        self.card_frame.setStyleSheet("QFrame { background-color: #FFFFFF; border-radius: 12px; border: 1px solid #EAEAEA; }")
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
            QPushButton { background-color: #8E44AD; color: white; border: none; border-radius: 6px; padding: 6px 16px; font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-weight: 600; font-size: 13px; }
            QPushButton:hover { background-color: #9B59B6; }
        """)
        btn_layout.addWidget(self.close_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        full_text = "\n\n".join(self.translated_paragraphs)
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
            "understand the full context correctly.\n\n"
            "Here is another paragraph. It is completely separate."
        )

    # ================= 核心修改区域：完美段落处理 =================
    # 1. 修复连字符断字 (trans-\nlate -> translate)
    text = re.sub(r'-\s*\n\s*', '', text_to_translate)
    
    # 2. 智能合并 PDF 复制带来的多余换行 (仅保留真正的段落换行)
    # 如果换行符前面不是句号等结束标点，且后面不是换行符，则视为空格合并
    text = re.sub(r'(?<![.!?。！？>”"])\n(?!\n)', ' ', text)
    
    # 3. 按真实段落分割
    original_paragraphs = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
    
    # 4. 组合发送给大模型 (段落之间用换行符隔开)
    final_text_for_ai = '\n'.join(original_paragraphs)
    # ==========================================================\

    # 全文翻译 (调用 Gemini)
    res_text = translate_text(final_text_for_ai)
    
    if "翻译出错" not in res_text:
        translated_paragraphs = [p.strip() for p in res_text.split('\n') if p.strip()]
    else:
        translated_paragraphs = [res_text]

    # 启动 UI
    app = QApplication(sys.argv)
    window = TranslationWindow(original_paragraphs, translated_paragraphs)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
