import sys
import os
from urllib.parse import unquote
# 引入 html 库用于转义字符，防止原文中包含 < > 等符号破坏 HTML 结构
import html 
from deep_translator import GoogleTranslator

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QTextEdit, 
                             QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QSizePolicy)
from PyQt6.QtGui import QColor, QScreen, QCursor
from PyQt6.QtCore import Qt

# ================= 配置区域 =================
PROXY_URL = 'http://127.0.0.1:7897' 
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
# ===========================================

class TranslationWindow(QWidget):
    def __init__(self, original_text, translated_text):
        super().__init__()
        self.original_text = original_text
        self.translated_text = translated_text
        self.initUI()
        self.copy_to_clipboard()

    def text_to_html(self, text, is_translation=False):
        """
        优化后的 HTML 生成：
        统一优先使用 Segoe UI 渲染英文，微软雅黑渲染中文。
        """
        safe_text = html.escape(text)
        paragraphs = [p for p in safe_text.split('\n') if p.strip()]
        
        # 核心修改：字体栈 (Font Stack)
        # 1. Segoe UI: 负责英文、数字、标点 (Windows 原生西文字体)
        # 2. Microsoft YaHei UI: 负责中文 (当 Segoe UI 缺字时自动替补)
        font_family = "'Segoe UI', 'Microsoft YaHei UI', sans-serif"

        if is_translation:
            # === 译文样式 ===
            # 中文阅读：行高 1.4，段间距 2px，颜色深灰
            style = "margin-bottom: 2px; line-height: 1.4; text-align: justify;"
            html_content = "".join([f'<p style="{style}">{p}</p>' for p in paragraphs])
            
            return f'<div style="color: #2c3e50; font-family: {font_family}; font-size: 15px;">{html_content}</div>'
        else:
            # === 原文样式 ===
            # 英文阅读：行高 1.45，颜色浅灰
            style = "margin-bottom: 8px; line-height: 1.45;"
            html_content = "".join([f'<p style="{style}">{p}</p>' for p in paragraphs])
            
            return f'<div style="color: #606266; font-family: {font_family}; font-size: 13px;">{html_content}</div>'

    def initUI(self):
        # 1. 窗口基础设置
        self.setWindowTitle('Google 翻译')
        self.resize(720, 750) # 稍微加宽，增加呼吸感
        self.setStyleSheet("background-color: #F5F7FA;") 
        
        # 窗口居中
        center_point = QScreen.availableGeometry(QApplication.primaryScreen()).center()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        # 2. 主布局
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(25, 25, 25, 25) # 增加边距
        main_layout.setSpacing(15)

        # ================= 卡片区域 =================
        self.card_frame = QFrame()
        self.card_frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-radius: 16px; /* 更圆润的边角 */
                border: 1px solid #EAEAEA;
            }
        """)
        
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(25)
        shadow.setYOffset(5)
        shadow.setColor(QColor(0, 0, 0, 15)) # 更淡更高级的阴影
        self.card_frame.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card_frame)
        card_layout.setContentsMargins(25, 25, 25, 25)
        card_layout.setSpacing(12)

        # --- A. 原文部分 (辅助信息) ---
        lbl_origin = QLabel("ORIGINAL TEXT") # 全大写更具设计感
        lbl_origin.setStyleSheet("""
            color: #909399; 
            font-size: 11px; 
            font-weight: 700; 
            letter-spacing: 1px; /* 增加字间距 */
        """)
        card_layout.addWidget(lbl_origin)

        self.txt_origin = QTextEdit()
        # 【关键修改】使用 setHtml 而不是 setPlainText
        self.txt_origin.setHtml(self.text_to_html(self.original_text, is_translation=False))
        self.txt_origin.setReadOnly(True)
        self.txt_origin.setMaximumHeight(100)
        # 样式：移除默认边框，左侧增加一条装饰线，增加层次感
        self.txt_origin.setStyleSheet("""
            QTextEdit {
                background-color: #FAFAFA; /* 极淡的灰色背景区分 */
                border: none;
                border-left: 3px solid #DCDFE6; /* 左侧装饰线 */
                padding-left: 8px; /* 文字离装饰线的距离 */
                font-size: 13px;
            }
        """)
        card_layout.addWidget(self.txt_origin)

        # --- 分割线 (虚线更轻盈) ---
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: transparent; border-top: 1px dashed #E0E0E0; max-height: 1px; margin: 5px 0;")
        card_layout.addWidget(line)

        # --- B. 译文部分 (核心信息) ---
        lbl_result = QLabel("TRANSLATION")
        lbl_result.setStyleSheet("""
            color: #409EFF; 
            font-size: 11px; 
            font-weight: 700; 
            letter-spacing: 1px;
            margin-top: 5px;
        """)
        card_layout.addWidget(lbl_result)

        self.txt_result = QTextEdit()
        # 【关键修改】使用 setHtml 处理译文
        self.txt_result.setHtml(self.text_to_html(self.translated_text, is_translation=True))
        self.txt_result.setReadOnly(True)
        
        # 样式：纯净背景，强调内容
        self.txt_result.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                border: none;
                font-size: 16px;
            }
            /* 滚动条样式保持不变，因为很好看 */
            QScrollBar:vertical {
                border: none;
                background: #F0F0F0;
                width: 6px;
                margin: 0px;
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
        card_layout.addWidget(self.txt_result)

        main_layout.addWidget(self.card_frame)

        # ================= 底部按钮区域 =================
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        self.close_btn = QPushButton("Copy & Close") # 英文按钮更简洁
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #409EFF;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                /* 统一使用 Segoe UI 优先 */
                font-family: 'Segoe UI', 'Microsoft YaHei UI'; 
                font-size: 14px;
                font-weight: 600; /* Segoe UI 的半粗体 (Semibold) 很好看 */
            }
            QPushButton:hover {
                background-color: #66B1FF;
            }
            QPushButton:pressed {
                background-color: #3A8EE6;
            }
        """)
        btn_layout.addWidget(self.close_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.translated_text)

def main():
    # 1. 参数处理逻辑
    if len(sys.argv) > 1:
        raw_text = " ".join(sys.argv[1:])
        clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
        text_to_translate = unquote(clean_text)
    else:
        text_to_translate = "Hello world, this is a test for the new UI design."

    # 2. 翻译逻辑
    try:
        translated_text = GoogleTranslator(source='auto', target='zh-CN').translate(text_to_translate)
        final_content = translated_text
    except Exception as e:
        final_content = f"翻译出错: {str(e)}"

    # 3. 启动 PyQt 界面
    app = QApplication(sys.argv)
    
    # 传入 原文 和 译文 两个参数
    window = TranslationWindow(text_to_translate, final_content)
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
