import sys
import os
from urllib.parse import unquote
from deep_translator import GoogleTranslator

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QTextEdit, 
                             QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout)
from PyQt6.QtGui import (QColor, QScreen, QTextCursor, QTextCharFormat, 
                         QPalette, QTextBlockFormat, QFont, QPainter, QBrush, QPen) # 新增最后三个
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QPoint

# ================= 配置区域 =================
# 如果你的网络不需要代理，请注释掉这几行
PROXY_URL = 'http://127.0.0.1:7897' 
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
# ===========================================

# ================= 1. 后台查词线程 =================
class DictionaryThread(QThread):
    """
    后台线程：用于查询单词或短语的释义，避免卡死主界面
    """
    result_ready = pyqtSignal(str, str) # 信号：(原文, 译文)

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        try:
            # 这里复用 GoogleTranslator，也可以换成专门的字典 API
            translator = GoogleTranslator(source='auto', target='zh-CN')
            res = translator.translate(self.text)
            self.result_ready.emit(self.text, res)
        except Exception as e:
            self.result_ready.emit(self.text, "查询失败")

# ================= 2. 自定义悬浮气泡 =================
class PopupLabel(QLabel):
    """
    美化的悬浮气泡，手动绘制背景以完美支持圆角
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        # 1. 必须保留这个属性，否则圆角外会有黑色直角
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # 2. 样式表只负责文字和内边距，背景色由 paintEvent 绘制
        self.setStyleSheet("""
            QLabel {
                color: #2c3e50;
                padding: 8px 12px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
            }
        """)
        
        # 添加阴影
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)
        self.hide()

    def paintEvent(self, event):
        """
        核心修复：手动绘制圆角背景
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing) # 抗锯齿，保证圆角平滑
        
        # 设置背景色 (深灰)
        painter.setBrush(QBrush(QColor("#effdff")))
        # 设置边框色 (稍浅的灰)
        painter.setPen(QPen(QColor("#4B4D51"), 1))
        
        # 绘制圆角矩形
        # rect() 是控件大小，adjusted 是为了防止边框被切掉一半
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(rect, 6, 6) # 6px 圆角
        
        # 绘制完背景后，调用父类方法绘制文字
        super().paintEvent(event)

    def show_message(self, text, global_pos):
        self.setText(text)
        self.adjustSize()
        self.move(global_pos.x(), global_pos.y() - self.height() - 10)
        self.show()
        self.raise_()


# ================= 3. 增强版文本框 =================
class InteractiveTextEdit(QTextEdit):
    """
    支持：鼠标悬停段落高亮 + 自动滚动 + 划词翻译
    """
    hover_index_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.segment_ranges = []    # [(start, end), ...]
        self.current_highlight_index = -1
        
        # 初始化悬浮窗和线程
        self.popup = PopupLabel() 
        self.dict_thread = None
        
        # 优化滚动条样式 + 设置选中文本颜色
        self.setStyleSheet("""
            QTextEdit {
                border: none;
                background-color: transparent;
                selection-background-color: #B3D8FF; /* 选中文本背景色：淡蓝 */
                selection-color: #303133;            /* 选中文本前景色：深灰 */
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
        """
        鼠标移动事件：
        1. 如果没有按住鼠标（单纯移动），执行段落高亮检测。
        2. 如果按住了鼠标（正在划选），暂停段落高亮检测，避免干扰。
        """
        if event.buttons() == Qt.MouseButton.NoButton:
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

    def mouseReleaseEvent(self, event):
        """
        鼠标释放事件：检测是否有选中文本，如果有则触发查词
        """
        super().mouseReleaseEvent(event)
        
        cursor = self.textCursor()
        selected_text = cursor.selectedText().strip()

        # 1. 如果有选中文本，且长度适中（过滤掉误触或全选整段）
        if selected_text and len(selected_text) < 80:
            # 获取选区矩形位置，用于定位弹窗
            rect = self.cursorRect(cursor)
            # mapToGlobal 将窗口坐标转换为屏幕绝对坐标
            global_pos = self.mapToGlobal(rect.topLeft()) 
            
            self.start_lookup(selected_text, global_pos)
        else:
            # 点击空白处或取消选择时，隐藏弹窗
            self.popup.hide()

    def start_lookup(self, text, pos):
        """启动线程查询释义"""
        # 停止之前的线程（如果还在跑）
        if self.dict_thread and self.dict_thread.isRunning():
            self.dict_thread.terminate()
            self.dict_thread.wait()

        # 启动新线程
        self.dict_thread = DictionaryThread(text)
        # 这里的 lambda 用于将 pos 传递给回调函数
        self.dict_thread.result_ready.connect(lambda orig, trans: self.show_popup_result(orig, trans, pos))
        self.dict_thread.start()

    def show_popup_result(self, original, translation, pos):
        """显示查询结果"""
        # [修改] 注释掉下面这两行。
        # 解释：只要发起了请求，无论鼠标是否移走或选区是否还在，都应该显示结果。
        # if not self.textCursor().hasSelection():
        #     return
            
        display_text = f"{original}\n⬇\n{translation}"
        self.popup.show_message(display_text, pos)

    def closeEvent(self, event):
        """窗口关闭时销毁弹窗"""
        self.popup.close()
        super().closeEvent(event)

    def highlight_segment(self, index):
        """高亮指定段落（保持原有逻辑）"""
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


# ================= 4. 主窗口逻辑 (基本保持不变) =================
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
        text_edit.clear()
        cursor = text_edit.textCursor()
        ranges = []
        
        # 1. 字体栈
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        
        block_fmt = QTextBlockFormat()
        
        if is_translation:
            # 译文样式
            font.setPixelSize(15) 
            color = QColor("#2c3e50")
            block_fmt.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            block_fmt.setBottomMargin(12) 
        else:
            # 原文样式
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
        # 联动高亮逻辑
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
            
        self.setWindowTitle('Google 翻译 (支持划词)')
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

        # --- A. 原文部分 ---\
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

        # --- 分割线 ---\
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: transparent; border-top: 1px dashed #E0E0E0; max-height: 1px; margin: 4px 0;")
        card_layout.addWidget(line)

        # --- B. 译文部分 ---\
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
            "The quick brown fox jumps over the lazy dog. "
            "This is a sentence that spans across \n"
            "multiple lines to test if the translator can \n"
            "understand the full context correctly."
        )

    # 1. 预处理原文
    original_segments = text_to_translate.split('\n')
    
    # 2. 全文翻译
    try:
        translator = GoogleTranslator(source='auto', target='zh-CN')
        res_text = translator.translate(text_to_translate)
        
        if res_text:
            translated_segments = res_text.split('\n')
        else:
            translated_segments = [""]
            
    except Exception as e:
        translated_segments = [f"Error: {str(e)}"]

    app = QApplication(sys.argv)
    window = TranslationWindow(original_segments, translated_segments)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
