import sys
import os
import re
import warnings
from urllib.parse import unquote
from google import genai # [更新] 引入新的 SDK

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QTextEdit, 
                             QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout)
from PyQt6.QtGui import (QColor, QScreen, QTextCursor, QTextCharFormat, 
                         QTextBlockFormat, QFont)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

warnings.filterwarnings("ignore")

# ================= 配置区域 =================
GOOGLE_API_KEY = "AIzaSyB1TLMbTWPAxust0rCcqcWPOGJjvtIYlmg"

PROXY_URL = 'http://127.0.0.1:7897'
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 

# [更新] 使用新的 Client 初始化方式
client = genai.Client(api_key=GOOGLE_API_KEY.strip())
MODEL_NAME = 'gemma-3-27b-it'
# ===========================================

# ================= 1. 后台流式翻译/查词线程 =================
class TranslationThread(QThread):
    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(bool, str) # success, error_message

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        try:
            text_clean = self.text.strip()
            # 智能判断：如果单词数 <= 5 且字符总长度 < 50，则认为是查单词/短语
            words = text_clean.split()
            is_dictionary_mode = len(words) <= 5 and len(text_clean) < 50

            if is_dictionary_mode:
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

            # [更新] 使用新的 generate_content_stream 方法实现流式输出
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

# ================= 2. 后台查词线程 =================
class DictionaryThread(QThread):
    result_ready = pyqtSignal(str, str)

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        try:
            # 【优化】使用结构化 Prompt，强制 AI 按固定格式输出，避免废话
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
            self.result_ready.emit(self.text, response.text.strip())
        except Exception as e:
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

# ================= 4. 纯净版文本框 (保留划词，移除悬停) =================
class InteractiveTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.popup = PopupLabel() 
        self.dict_thread = None
        
        self.setStyleSheet("""
            QTextEdit { border: none; background-color: transparent; selection-background-color: #B3D8FF; selection-color: #303133; }
            QScrollBar:vertical { border: none; background: #F0F0F0; width: 6px; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #C0C4CC; min-height: 20px; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #909399; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
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

# ================= 5. 主窗口逻辑 =================
class TranslationWindow(QWidget):
    def __init__(self, original_paragraphs):
        super().__init__()
        self.original_paragraphs = original_paragraphs
        self.full_translation = ""
        self.initUI()
        
        # 填充原文
        self.populate_original_text()
        # 初始化译文框的样式
        self.setup_result_format()
        
        # 启动流式翻译
        self.start_translation()

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

    def setup_result_format(self):
        """预先设置译文框的字体和段落格式"""
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

    def start_translation(self):
        # 1. 启动时先插入一个初始光标，告诉用户 "我在思考"
        cursor = self.txt_result.textCursor()
        cursor.insertText(" ▍", self.result_char_fmt)

        # 2. 启动后台线程
        final_text_for_ai = '\n'.join(self.original_paragraphs)
        self.trans_thread = TranslationThread(final_text_for_ai)
        self.trans_thread.chunk_received.connect(self.append_translation_chunk)
        self.trans_thread.finished.connect(self.on_translation_finished)
        self.trans_thread.start()

    def append_translation_chunk(self, chunk):
        """接收到流式片段时，追加到文本框并维持光标在末尾"""
        self.full_translation += chunk
        cursor = self.txt_result.textCursor()
        
        # 1. 移动到末尾，选中并删除之前的光标 " ▍" (2个字符)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, 2)
        if cursor.selectedText() == " ▍":
            cursor.removeSelectedText()
        else:
            # 容错：如果没找到光标，确保位置在最后
            cursor.movePosition(QTextCursor.MoveOperation.End)
        
        # 2. 插入新接收到的文本
        cursor.setBlockFormat(self.result_block_fmt)
        cursor.insertText(chunk, self.result_char_fmt)
        
        # 3. 在末尾重新加上光标
        cursor.insertText(" ▍", self.result_char_fmt)
        
        self.txt_result.setTextCursor(cursor)
        self.txt_result.ensureCursorVisible()

    def on_translation_finished(self, success, error_msg):
        # 1. 翻译结束，清理掉最后的光标
        cursor = self.txt_result.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, 2)
        if cursor.selectedText() == " ▍":
            cursor.removeSelectedText()

        # 2. 处理错误或完成状态
        if not success:
            self.append_translation_chunk(f"\n\n[翻译出错: {error_msg}]")
        
        self.copy_to_clipboard()
        self.close_btn.setText("Copy & Close")
        self.close_btn.setEnabled(True)

    def initUI(self):
        # 获取纯净的原文文本
        total_text = "\n".join(self.original_paragraphs).strip()
        text_len = len(total_text)
        words = total_text.split()
        
        # 预判是否为词典模式 (与后台线程的判断逻辑保持一致)
        is_dictionary_mode = len(words) <= 5 and text_len < 50
        
        base_width = 500 
        
        # 动态计算高度
        if is_dictionary_mode:
            # 词典模式：虽然原文短，但需要展示音标、释义和例句，给予更大的基础高度
            base_height = 700 
        elif text_len < 50: 
            # 普通短句翻译
            base_height = 350
        elif text_len < 200: 
            # 中等段落
            base_height = 500
        else: 
            # 长文翻译
            base_height = 700
            
        self.setWindowTitle('Gemini 翻译 (流式输出)')
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
        card_layout.addWidget(self.txt_result)

        main_layout.addWidget(self.card_frame)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        btn_layout.addStretch(1)

        self.close_btn = QPushButton("Translating...") 
        self.close_btn.setEnabled(False) # 翻译完成前禁用或显示状态
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        self.close_btn.setStyleSheet("""
            QPushButton { background-color: #8E44AD; color: white; border: none; border-radius: 6px; padding: 6px 16px; font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-weight: 600; font-size: 13px; }
            QPushButton:hover { background-color: #9B59B6; }
            QPushButton:disabled { background-color: #C39BD3; }
        """)
        btn_layout.addWidget(self.close_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    def closeEvent(self, event):
        """窗口关闭时的安全清理机制"""
        # 如果后台翻译线程还在运行，强制终止它，防止后台僵尸网络请求和崩溃
        if hasattr(self, 'trans_thread') and self.trans_thread.isRunning():
            self.trans_thread.terminate()
            self.trans_thread.wait()
            
        # 调用父类的关闭事件，正常销毁窗口
        super().closeEvent(event)

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        if self.full_translation:
            clipboard.setText(self.full_translation.strip())

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
    text = re.sub(r'-\s*\n\s*', '', text_to_translate)
    text = re.sub(r'(?<![.!?。！？>”"])\n(?!\n)', ' ', text)
    original_paragraphs = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
    # ==========================================================

    # 启动 UI (不再阻塞等待翻译)
    app = QApplication(sys.argv)
    window = TranslationWindow(original_paragraphs)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
