import warnings
warnings.filterwarnings("ignore")  # 强行屏蔽所有警告
import os
import sys
import google.generativeai as genai
import tkinter as tk
from tkinter import messagebox
from google.api_core import client_options
# 1. 新增：引入解码工具
from urllib.parse import unquote

# ================= 配置区域 =================
# ⚠️ 请填入你的 API Key
GOOGLE_API_KEY = "AIzaSyDhCx-m-xyYmY5r_5bE3VMIUWbek_-UTHA"

# 代理设置
PROXY_URL = 'http://127.0.0.1:7897'
os.environ['HTTPS_PROXY'] = PROXY_URL
os.environ['HTTP_PROXY'] = PROXY_URL
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 

# ===========================================

# 配置 Gemini
genai.configure(api_key=GOOGLE_API_KEY.strip(), transport='rest')
model = genai.GenerativeModel('gemma-3-27b-it')

def translate_text(text):
    try:
        # 使用更严格的 Prompt，防止 AI 废话
        prompt = f"""
        你是一个翻译引擎。请将下方的文本翻译成简体中文。
        规则：
        1. 直接输出译文，不要解释，不要加前缀。
        2. 如果文本包含乱码或无法翻译，请原样保留。
        
        待翻译文本：
        {text}
        """
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"翻译出错: {str(e)}"

def show_result(result):
    root = tk.Tk()
    root.withdraw() 
    root.attributes('-topmost', True)
    messagebox.showinfo("Gemini 翻译结果", result)
    root.destroy()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # 1. 获取原始参数
        raw_text = " ".join(sys.argv[1:])
        
        # 2. 清洗数据 (关键步骤)
        # 去除 SnipDo 可能添加的奇怪前缀
        clean_text = raw_text.replace("-URLENCODED_ALT_TEXT", "").strip()
        
        # 3. URL 解码 (把 %20 变回空格，把乱码变回中文)
        decoded_text = unquote(clean_text)
        
        # 4. 发送给 AI
        translation = translate_text(decoded_text)
        show_result(translation)
    else:
        # 本地测试
        print("正在进行本地测试...")
        # 模拟一个带编码的测试文本
        test_text = "Hello%20world%2C%20this%20is%20a%20test."
        decoded = unquote(test_text)
        print(f"解码后: {decoded}")
        
        test_result = translate_text(decoded)
        print(f"测试结果: {test_result}")
        show_result(test_result)
