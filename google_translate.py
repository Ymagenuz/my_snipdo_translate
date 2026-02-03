import sys
import os
from urllib.parse import unquote
from deep_translator import GoogleTranslator

# 引入 PyQt6 库
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel
from PyQt6.QtGui import QFont, QIcon, QScreen
from PyQt6.QtCore import Qt

# ================= 配置区域 =================
PROXY_URL = 'http://127.0.0.1:7897' 
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
# ===========================================

class TranslationWindow(QWidget):
    def __init__(self, content):
        super().__init__()
        self.content = content
        self.initUI()
        self.copy_to_clipboard()

    def initUI(self):
        # 1. 窗口基础设置
        self.setWindowTitle('Google 翻译结果')
        self.resize(600, 420)
        self.setStyleSheet("background-color: #F3F3F3;") # 整体背景灰白
        
        # 窗口居中逻辑
        center_point = QScreen.availableGeometry(QApplication.primaryScreen()).center()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())
        
        # 窗口置顶
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        # 2. 布局管理器 (垂直布局)
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15) # 页边距
        layout.setSpacing(10) # 控件间距

        # 3. 文本框 (核心组件)
        self.text_edit = QTextEdit()
        
        # === 核心功能：只读但可选中 ===
        self.text_edit.setReadOnly(True) 
        
        # === 核心美化：使用 CSS 设置样式 ===
        # line-height: 160% -> 解决“字堆在一起”
        # font-family -> 优先用 Segoe UI (英文好看)，后备 Microsoft YaHei (中文)
        # padding -> 内边距
        # border -> 去掉边框，更现代
        css_style = """
            QTextEdit {
                background-color: #FFFFFF;
                color: #333333;
                border: 1px solid #E0E0E0;
                border-radius: 8px;
                padding: 15px;
                font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
                font-size: 16px;
                line-height: 160%; 
            }
        """
        self.text_edit.setStyleSheet(css_style)
        
        # 设置文本 (PyQt 支持 HTML，所以我们可以用 HTML 进一步微调，也可以直接设纯文本)
        self.text_edit.setPlainText(self.content)
        
        layout.addWidget(self.text_edit)

        # 4. 底部按钮
        self.close_btn = QPushButton("关闭 (已自动复制)")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        
        # 按钮样式
        btn_style = """
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 6px;
                padding: 10px;
                font-family: 'Microsoft YaHei UI';
                font-size: 14px;
                color: #555555;
            }
            QPushButton:hover {
                background-color: #E6E6E6;
                color: #000000;
            }
        """
        self.close_btn.setStyleSheet(btn_style)
        layout.addWidget(self.close_btn)

        self.setLayout(layout)

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.content)

def main():
    # 1. 参数处理逻辑 (保持不变)
    if len(sys.argv) > 1:
        raw_text = " ".join(sys.argv[1:])
        clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
        text_to_translate = unquote(clean_text)
    else:
        text_to_translate = "Hello world"

    # 2. 翻译逻辑
    try:
        translated_text = GoogleTranslator(source='auto', target='zh-CN').translate(text_to_translate)
        final_content = translated_text
    except Exception as e:
        final_content = f"翻译出错: {str(e)}\n\n原始文本: {text_to_translate}"

    # 3. 启动 PyQt 界面
    app = QApplication(sys.argv)
    
    # 设置全局高分屏支持 (防止在 4K 屏上模糊)
    # PyQt6 默认处理得比较好，通常不需要额外设置
    
    window = TranslationWindow(final_content)
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
