import sys
import os
from urllib.parse import unquote
from deep_translator import GoogleTranslator

# 引入 PyQt6 库
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

    def initUI(self):
        # 1. 窗口基础设置
        self.setWindowTitle('Google 翻译')
        self.resize(700, 600) # 稍微调高一点，容纳更多层次
        self.setStyleSheet("background-color: #F5F7FA;") # 使用更柔和的灰蓝色背景
        
        # 窗口居中
        center_point = QScreen.availableGeometry(QApplication.primaryScreen()).center()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())
        
        # 窗口置顶
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        # 2. 主布局
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # ================= 卡片区域 (包含原文和译文) =================
        self.card_frame = QFrame()
        self.card_frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-radius: 12px;
                border: 1px solid #EAEAEA;
            }
        """)
        
        # 添加阴影效果 (让卡片浮起来)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setXOffset(0)
        shadow.setYOffset(4)
        shadow.setColor(QColor(0, 0, 0, 20)) # 淡淡的黑色阴影
        self.card_frame.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card_frame)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)

        # --- A. 原文部分 (辅助信息) ---
        lbl_origin = QLabel("原文 / Original")
        lbl_origin.setStyleSheet("color: #909399; font-size: 12px; font-weight: bold; border: none;")
        card_layout.addWidget(lbl_origin)

        self.txt_origin = QTextEdit()
        self.txt_origin.setPlainText(self.original_text)
        self.txt_origin.setReadOnly(True)
        self.txt_origin.setMaximumHeight(80) # 限制高度，不要喧宾夺主
        # 样式：灰色文字，背景透明，无边框
        self.txt_origin.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                color: #606266;
                border: none;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 13px;
                line-height: 140%;
            }
        """)
        card_layout.addWidget(self.txt_origin)

        # --- 分割线 ---
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #EBEEF5; border: none; max-height: 1px;")
        card_layout.addWidget(line)

        # --- B. 译文部分 (核心信息) ---
        lbl_result = QLabel("译文 / Translation")
        lbl_result.setStyleSheet("color: #409EFF; font-size: 12px; font-weight: bold; border: none; margin-top: 5px;")
        card_layout.addWidget(lbl_result)

        self.txt_result = QTextEdit()
        self.txt_result.setPlainText(self.translated_text)
        self.txt_result.setReadOnly(True)
        # 样式：深色文字，大字号，美化滚动条
        self.txt_result.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                color: #303133;
                border: none;
                font-family: 'Segoe UI', 'Microsoft YaHei UI';
                font-size: 16px;
                font-weight: 500;
                line-height: 160%;
            }
            /* 美化滚动条 */
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

        # 将卡片添加到主布局
        main_layout.addWidget(self.card_frame)

        # ================= 底部按钮区域 =================
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1) # 弹簧，把按钮顶到右边

        self.close_btn = QPushButton("关闭 (已复制)")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        
        # 按钮样式：蓝色主色调，更现代
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #409EFF;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-family: 'Microsoft YaHei UI';
                font-size: 14px;
                font-weight: bold;
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
