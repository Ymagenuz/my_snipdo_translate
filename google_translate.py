import sys
import os
import tkinter as tk
from deep_translator import GoogleTranslator
# 1. 引入解码库，专门对付 %20 这种符号
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
        # 🧹 清洗数据 (关键步骤)
        # ===========================================
        
        # 1. 去掉 SnipDo 可能添加的奇怪前缀
        clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
        
        # 2. 进行 URL 解码
        text_to_translate = unquote(clean_text)
        
    else:
        text_to_translate = "Hello world"

    try:
        # 3. 翻译清洗后的文本
        translated_text = GoogleTranslator(source='auto', target='zh-CN').translate(text_to_translate)
        
        show_popup(translated_text)

    except Exception as e:
        show_popup(f"翻译出错: {str(e)}\n\n原始文本: {text_to_translate}")

def show_popup(content):
    """
    创建一个自定义窗口，包含可复制的文本框 (UI + 中英文混排优化版)
    """
    root = tk.Tk()
    root.title("Google 翻译结果")
    
    # === 窗口设置 ===
    window_width = 600
    window_height = 400
    
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x_cordinate = int((screen_width/2) - (window_width/2))
    y_cordinate = int((screen_height/2) - (window_height/2))
    
    root.geometry(f"{window_width}x{window_height}+{x_cordinate}+{y_cordinate}")
    root.attributes('-topmost', True)
    root.configure(bg="#F5F5F5") 

    # === 自动复制 ===
    try:
        root.clipboard_clear()
        root.clipboard_append(content)
        root.update()
    except:
        pass

    # === UI 布局 ===
    text_frame = tk.Frame(root, bg="#F5F5F5", padx=10, pady=10)
    text_frame.pack(expand=True, fill='both')

    scrollbar = tk.Scrollbar(text_frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # 1. 基础文本框 (默认使用中文字体)
    # 注意：这里 font 设置的是全局基础字体（推荐微软雅黑）
    base_font_size = 12
    text_area = tk.Text(
        text_frame, 
        font=("微软雅黑", base_font_size), 
        bg="#FFFFFF",
        fg="#333333",
        wrap=tk.WORD, 
        padx=15, pady=15,
        spacing1=10,
        spacing2=6,
        spacing3=5,
        relief=tk.FLAT,
        yscrollcommand=scrollbar.set
    )
    text_area.pack(side=tk.LEFT, expand=True, fill='both')
    scrollbar.config(command=text_area.yview)
    
    # 插入内容
    text_area.insert(tk.END, content)

    # ==========================================
    # 🎨 核心修改：配置英文专用样式 (Tag)
    # ==========================================
    
    # 定义一个名为 "english_style" 的标签
    # 字体：Segoe UI (Windows原生英文字体) 或 Arial，看起来比微软雅黑的英文更紧凑、现代
    # 颜色：稍微深一点点，或者保持一致
    text_area.tag_config("english_style", font=("Segoe UI", base_font_size))
    
    # === 自动查找英文并应用样式 ===
    # 逻辑：从头搜到尾，找到所有 [a-zA-Z0-9] 也就是字母和数字
    start_pos = "1.0"
    while True:
        # 搜索正则表达式匹配的字符
        # count=count_var 用来记录匹配到的长度
        count_var = tk.IntVar()
        pos = text_area.search(r'[a-zA-Z0-9\.]+', start_pos, stopindex=tk.END, count=count_var, regexp=True)
        
        if not pos:
            break
            
        # 计算结束位置 (例如 "1.0" + 5个字符 = "1.5")
        end_pos = f"{pos}+{count_var.get()}c"
        
        # 给这段文本加上 "english_style" 标签
        text_area.tag_add("english_style", pos, end_pos)
        
        # 更新下一次搜索的起始位置
        start_pos = end_pos

    # ==========================================

    # 底部按钮
    btn_frame = tk.Frame(root, pady=8, bg="#F5F5F5")
    btn_frame.pack(fill='x')

    def close_window():
        root.destroy()
        
    tk.Button(
        btn_frame, 
        text="关闭 (已自动复制)", 
        command=close_window, 
        height=2, 
        bg="#E0E0E0",
        relief=tk.GROOVE,
        font=("微软雅黑", 10)
    ).pack(fill='x', padx=20)

    root.mainloop()



if __name__ == "__main__":
    main()
