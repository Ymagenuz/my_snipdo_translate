import sys
import os
from urllib.parse import unquote
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
        safe_text = html.escape(text)
        paragraphs = [p for p in safe_text.split('\n') if p.strip()]
        
        # 字体栈：英文优先 Segoe UI，中文回退到微软雅黑
        font_family = "'Segoe UI', 'Microsoft YaHei UI', sans-serif"

        if is_translation:
            # 译文：行高 1.5 (稍微紧凑一点点)，段间距 10px
            style = "margin-bottom: 10px; line-height: 1.5; text-align: justify;"
            html_content = "".join([f'<p style="{style}">{p}</p>' for p in paragraphs])
            return f'<div style="color: #2c3e50; font-family: {font_family}; font-size: 15px;">{html_content}</div>'
        else:
            # 原文：行高 1.4
            style = "margin-bottom: 6px; line-height: 1.4;"
            html_content = "".join([f'<p style="{style}">{p}</p>' for p in paragraphs])
            return f'<div style="color: #606266; font-family: {font_family}; font-size: 13px;">{html_content}</div>'

    def initUI(self):
        # 1. 智能计算窗口大小
        # 基础宽度 680 (比之前窄一点，更精致)
        # 高度根据字数动态调整：少于100字用小窗口，多于100字用大窗口
        text_len = len(self.translated_text)
        base_width = 680
        if text_len < 50:
            base_height = 350
        elif text_len < 200:
            base_height = 480
        else:
            base_height = 650
            
        self.setWindowTitle('Google 翻译')
        self.resize(base_width, base_height)
        self.setStyleSheet("background-color: #F5F7FA;") 
        
        # 窗口居中
        center_point = QScreen.availableGeometry(QApplication.primaryScreen()).center()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(center_point)
        # 重新应用大小，防止居中计算后尺寸重置
        self.resize(base_width, base_height) 
        self.move(frame_geometry.topLeft())
        
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        # 2. 主布局 - 【关键修改】减少外部留白
        main_layout = QVBoxLayout()
        # 从 25 减少到 12，让卡片几乎填满窗口，减少"大窗口小内容"的感觉
        main_layout.setContentsMargins(12, 12, 12, 12) 
        main_layout.setSpacing(0) # 移除布局间距，由内部控制

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
        # 内部留白保持适中，给文字呼吸空间
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)

        # --- A. 原文部分 ---
        header_layout = QHBoxLayout()
        lbl_origin = QLabel("ORIGINAL")
        lbl_origin.setStyleSheet("color: #909399; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        header_layout.addWidget(lbl_origin)
        header_layout.addStretch()
        card_layout.addLayout(header_layout)

        self.txt_origin = QTextEdit()
        self.txt_origin.setHtml(self.text_to_html(self.original_text, is_translation=False))
        self.txt_origin.setReadOnly(True)
        # 【关键修改】限制原文高度，不要喧宾夺主
        self.txt_origin.setMaximumHeight(80) 
        self.txt_origin.setStyleSheet("""
            QTextEdit {
                background-color: #FAFAFA;
                border: none;
                border-left: 2px solid #E4E7ED;
                padding-left: 6px;
                font-size: 13px;
            }
        """)
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

        self.txt_result = QTextEdit()
        self.txt_result.setHtml(self.text_to_html(self.translated_text, is_translation=True))
        self.txt_result.setReadOnly(True)
        # 译文不设最大高度，让它填充剩余所有空间
        self.txt_result.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                border: none;
                font-size: 16px;
            }
            QScrollBar:vertical {
                border: none;
                background: #F0F0F0;
                width: 5px;
                border-radius: 2px;
            }
            QScrollBar::handle:vertical {
                background: #C0C4CC;
                min-height: 20px;
                border-radius: 2px;
            }
        """)
        card_layout.addWidget(self.txt_result)

        main_layout.addWidget(self.card_frame)

        # ================= 底部按钮区域 =================
        # 将按钮放入卡片内部还是外部？
        # 放在外部会让窗口看起来更长。为了紧凑，我们把按钮做得更扁平，紧贴卡片下方
        
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0) # 上方留一点空隙
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
                padding: 6px 16px; /* 减小按钮尺寸 */
                font-family: 'Segoe UI', 'Microsoft YaHei UI'; 
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #66B1FF; }
            QPushButton:pressed { background-color: #3A8EE6; }
        """)
        btn_layout.addWidget(self.close_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.translated_text)

def main():
    if len(sys.argv) > 1:
        raw_text = " ".join(sys.argv[1:])
        clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
        text_to_translate = unquote(clean_text)
    else:
        text_to_translate = "Hello world, this is a test for the new UI design."

    try:
        translated_text = GoogleTranslator(source='auto', target='zh-CN').translate(text_to_translate)
        final_content = translated_text
    except Exception as e:
        final_content = f"翻译出错: {str(e)}"

    app = QApplication(sys.argv)
    window = TranslationWindow(text_to_translate, final_content)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
