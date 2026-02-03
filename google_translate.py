import sys
import os
from urllib.parse import unquote
from deep_translator import GoogleTranslator

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QTextEdit, 
                             QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout)
from PyQt6.QtGui import (QColor, QScreen, QTextCursor, QTextCharFormat, 
                         QPalette, QTextBlockFormat, QFont)
from PyQt6.QtCore import Qt, pyqtSignal

# ================= 配置区域 =================
PROXY_URL = 'http://127.0.0.1:7897' 
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
# ===========================================

class InteractiveTextEdit(QTextEdit):
    """
    支持鼠标悬停检测 + 自动滚动的文本框
    """
    hover_index_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.segment_ranges = []    # [(start, end), ...]
        self.current_highlight_index = -1
        
        # 优化滚动条样式，使其更现代、不突兀
        self.setStyleSheet("""
            QTextEdit {
                border: none;
                background-color: transparent;
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
        cursor = self.cursorForPosition(event.pos())
        pos = cursor.position()
        
        found_index = -1
        # 查找当前位置属于哪一段
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

    def highlight_segment(self, index):
        """高亮指定段落"""
        if index < 0 or index >= len(self.segment_ranges):
            self.setExtraSelections([])
            return

        start, end = self.segment_ranges[index]
        
        if start == end: # 空行不高亮
            self.setExtraSelections([])
            return

        selection = QTextEdit.ExtraSelection()
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        selection.cursor = cursor
        
        fmt = QTextCharFormat()
        # 柔和的淡黄色高亮
        fmt.setBackground(QColor("#FFF8C5")) 
        selection.format = fmt
        # 全宽高亮，视觉更整洁
        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)

        self.setExtraSelections([selection])

    def scroll_to_segment(self, index):
        """滚动视图以确保指定段落可见"""
        if index < 0 or index >= len(self.segment_ranges):
            return

        start, _ = self.segment_ranges[index]
        cursor = self.textCursor()
        cursor.setPosition(start)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()


class TranslationWindow(QWidget):
    def __init__(self, original_segments, translated_segments):
        super().__init__()
        self.original_segments = original_segments
        self.translated_segments = translated_segments
        self.initUI()
        
        # 填充内容
        self.populate_text(self.txt_origin, self.original_segments, is_translation=False)
        self.populate_text(self.txt_result, self.translated_segments, is_translation=True)
        
        self.copy_to_clipboard()

    def populate_text(self, text_edit: InteractiveTextEdit, segments, is_translation=False):
        """
        核心修复：使用 setFamilies 和 setPixelSize 完美还原设计稿字体
        修复：setLineHeight 必须传入 int 类型 (.value)
        """
        text_edit.clear()
        cursor = text_edit.textCursor()
        ranges = []
        
        # 1. 字体栈：英文 Segoe UI 优先，中文雅黑候补
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        
        block_fmt = QTextBlockFormat()
        
        if is_translation:
            # 译文样式：15px, 1.5倍行高
            font.setPixelSize(15) 
            color = QColor("#2c3e50")
            # FIX: Added .value here
            block_fmt.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            block_fmt.setBottomMargin(12) 
        else:
            # 原文样式：13px, 1.4倍行高
            font.setPixelSize(13)
            color = QColor("#606266")
            # FIX: Added .value here
            block_fmt.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            block_fmt.setBottomMargin(6)
        
        char_fmt = QTextCharFormat()
        char_fmt.setFont(font)
        char_fmt.setForeground(color)

        for segment in segments:
            start_pos = cursor.position()
            
            # insertText 会保留所有空格和制表符
            cursor.insertText(segment, char_fmt)
            cursor.setBlockFormat(block_fmt)
            
            end_pos = cursor.position()
            ranges.append((start_pos, end_pos))
            
            cursor.insertBlock()
        
        text_edit.set_segments(ranges)
        text_edit.moveCursor(QTextCursor.MoveOperation.Start)

    def sync_highlight(self, index):
        self.txt_origin.highlight_segment(index)
        self.txt_origin.scroll_to_segment(index)
        
        self.txt_result.highlight_segment(index)
        self.txt_result.scroll_to_segment(index)

    def initUI(self):
        total_text = "".join(self.translated_segments)
        text_len = len(total_text)
        
        base_width = 720 
        if text_len < 50: base_height = 350
        elif text_len < 200: base_height = 500
        else: base_height = 700
            
        self.setWindowTitle('Google 翻译')
        self.resize(base_width, base_height)
        self.setStyleSheet("background-color: #F5F7FA;") 
        
        center_point = QScreen.availableGeometry(QApplication.primaryScreen()).center()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(center_point)
        self.resize(base_width, base_height) 
        self.move(frame_geometry.topLeft())
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(12, 12, 12, 12) 
        main_layout.setSpacing(0)

        # ================= 卡片区域 =================
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

        # --- A. 原文部分 ---
        header_layout = QHBoxLayout()
        lbl_origin = QLabel("ORIGINAL")
        lbl_origin.setStyleSheet("color: #909399; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        header_layout.addWidget(lbl_origin)
        header_layout.addStretch()
        card_layout.addLayout(header_layout)

        self.txt_origin = InteractiveTextEdit()
        self.txt_origin.setReadOnly(True)
        self.txt_origin.setMaximumHeight(120) 
        # 原文背景淡灰
        self.txt_origin.setStyleSheet("background-color: #FAFAFA; border-left: 2px solid #E4E7ED; padding-left: 4px;")
        self.txt_origin.hover_index_changed.connect(self.sync_highlight)
        card_layout.addWidget(self.txt_origin)

        # --- 分割线 ---
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: transparent; border-top: 1px dashed #E0E0E0; max-height: 1px; margin: 4px 0;")
        card_layout.addWidget(line)

        # --- B. 译文部分 ---
        lbl_result = QLabel("TRANSLATION")
        lbl_result.setStyleSheet("color: #409EFF; font-size: 10px; font-weight: 700; letter-spacing: 1px; margin-top: 2px;")
        card_layout.addWidget(lbl_result)

        self.txt_result = InteractiveTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setStyleSheet("background-color: transparent;")
        self.txt_result.hover_index_changed.connect(self.sync_highlight)
        card_layout.addWidget(self.txt_result)

        main_layout.addWidget(self.card_frame)

        # ================= 底部按钮 =================
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        btn_layout.addStretch(1)

        self.close_btn = QPushButton("Copy & Close") 
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #409EFF;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-weight: 600; font-size: 13px;
            }
            QPushButton:hover { background-color: #66B1FF; }
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
        # 测试用例
        text_to_translate = (
            "def calculate_sum(a, b):\n"
            "    # This function adds two numbers\n"
            "    result = a + b\n"
            "    return result\n"
            "\n"
            "Here is a normal paragraph to test the font rendering.\n"
            "中文测试：这里应该显示为微软雅黑，且清晰易读。"
        )

    # 1. 预处理
    original_segments = text_to_translate.split('\n')
    translated_segments = []
    
    # --- 优化开始 ---
    
    # 准备两个列表：一个存缩进，一个存待翻译的纯文本
    indents = []
    texts_to_send = []
    
    # 记录空行的索引，以便后续还原
    # (虽然 Google 可能会忽略空字符串，但为了对齐，我们手动处理)
    
    for seg in original_segments:
        if not seg.strip():
            indents.append("")
            texts_to_send.append("") # 占位
        else:
            stripped = seg.lstrip()
            indent = seg[:len(seg) - len(stripped)]
            indents.append(indent)
            texts_to_send.append(stripped)
            
    try:
        translator = GoogleTranslator(source='auto', target='zh-CN')
        
        # 过滤掉空行发送给 API (避免 API 报错或产生无用请求)
        # 记录非空行的原始索引，以便插回
        non_empty_indices = [i for i, t in enumerate(texts_to_send) if t]
        non_empty_texts = [texts_to_send[i] for i in non_empty_indices]
        
        if non_empty_texts:
            # === 核心优化：一次性批量翻译 ===
            # 这通常只消耗 1 次 HTTP 请求的时间
            translated_results = translator.translate_batch(non_empty_texts)
            
            # 将翻译结果填回对应位置
            final_results = [""] * len(original_segments)
            
            # 1. 填回翻译好的内容
            for i, idx in enumerate(non_empty_indices):
                # 容错：万一返回数量不一致（极少发生），做个保护
                if i < len(translated_results):
                    final_results[idx] = indents[idx] + translated_results[i]
                else:
                    final_results[idx] = indents[idx] + non_empty_texts[i] # 回退到原文
            
            # 2. 处理原本就是空行的
            for i in range(len(original_segments)):
                if i not in non_empty_indices:
                    final_results[i] = ""
                    
            translated_segments = final_results
        else:
            translated_segments = [""] * len(original_segments)

    except Exception as e:
        # 错误处理：如果批量翻译失败，显示错误信息
        translated_segments = [f"Error: {str(e)}"] * len(original_segments)

    # --- 优化结束 ---

    app = QApplication(sys.argv)
    window = TranslationWindow(original_segments, translated_segments)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
