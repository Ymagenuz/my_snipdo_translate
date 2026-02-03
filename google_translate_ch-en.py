import sys
import os
import tkinter as tk
from deep_translator import GoogleTranslator
# 1. 引入解码库
from urllib.parse import unquote

# ================= 配置区域 =================
PROXY_URL = 'http://127.0.0.1:7897' 
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
# ===========================================

def main():
    if len(sys.argv) > 1:
        # 获取原始参数
        raw_text = " ".join(sys.argv[1:])
        
        # ===========================================
        # 🧹 清洗数据
        # ===========================================
        
        # 1. 去掉 SnipDo 可能添加的前缀
        clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
        
        # 2. 进行 URL 解码
        text_to_translate = unquote(clean_text)
        
    else:
        text_to_translate = "你好，世界" # 默认测试文本

    try:
        # 3. 翻译：source='auto', target='en' (目标设为英文)
        translated_text = GoogleTranslator(source='auto', target='en').translate(text_to_translate)
        
        show_popup(translated_text)

    except Exception as e:
        show_popup(f"翻译出错: {str(e)}\n\n原始文本: {text_to_translate}")

def show_popup(content):
    """
    创建一个自定义窗口，包含可复制的文本框
    """
    root = tk.Tk()
    root.title("Google 翻译结果 (CN -> EN)") # 标题标明是中译英
    
    # 设置窗口大小 (宽x高)
    window_width = 500
    window_height = 350
    
    # 计算屏幕居中位置
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x_cordinate = int((screen_width/2) - (window_width/2))
    y_cordinate = int((screen_height/2) - (window_height/2))
    
    root.geometry(f"{window_width}x{window_height}+{x_cordinate}+{y_cordinate}")
    
    # 窗口置顶
    root.attributes('-topmost', True)

    # === 核心功能：自动复制到剪贴板 ===
    try:
        root.clipboard_clear()
        root.clipboard_append(content)
        root.update() # 保持剪贴板更新
    except:
        pass

    # === UI 布局 ===
    
    # 1. 创建文本框 (Text Widget)，字体稍微调大一点方便看英文
    text_area = tk.Text(root, font=("Arial", 12), wrap=tk.WORD, padx=10, pady=10)
    text_area.pack(expand=True, fill='both')
    
    # 插入翻译内容
    text_area.insert(tk.END, content)

    # 2. 底部按钮区域
    btn_frame = tk.Frame(root, pady=5)
    btn_frame.pack(fill='x')

    def close_window():
        root.destroy()
        
    # 关闭按钮
    tk.Button(btn_frame, text="关闭 (Result Copied)", command=close_window, height=2).pack(fill='x', padx=10)

    # 运行窗口
    root.mainloop()

if __name__ == "__main__":
    main()
