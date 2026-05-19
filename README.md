# SnipDo GPTSAPI 翻译工具

这是一个面向 Windows 和 SnipDo 的轻量翻译/OCR 工具。核心脚本 `gemini_translate.pyw` 使用 PyQt6 提供桌面窗口和系统托盘入口，通过 GPTSAPI 兼容的 OpenAI 接口调用 `gpt-5.4-nano` 完成翻译、查词和图片 OCR。

## 功能特性

- 支持手动输入或粘贴文本后翻译。
- 支持 SnipDo 选中文本后调用脚本并弹出翻译窗口。
- 支持自动识别中英及其他语言，默认中文译英文，英文或其他语言译简体中文。
- 支持查词模式，可输出语言、读音、对应表达、释义、用法和例句。
- 支持指定原文/目标语言：中文、英文、日文、韩文、法文、德文、西班牙文、俄文、意大利文。
- 支持剪贴板图片 OCR，并在识别后自动翻译。
- 支持单实例运行：多次从 SnipDo 调用时会复用已运行窗口。
- 支持系统托盘：显示主窗口、OCR 剪贴板图片、彻底退出。

## 项目结构

```text
.
├── gemini_translate.pyw                  # 主程序：PyQt6 UI、翻译、查词、OCR、单实例通信
├── snipdo_script_powershell_code/
│   ├── snipdo_gemini.txt                 # SnipDo 调用主程序的 PowerShell 示例
│   └── snipdo_google.txt                 # 旧版 Google 翻译脚本调用示例
├── snipdo_script_logo/                   # 托盘和 SnipDo 图标资源
├── legacy/                               # 旧版脚本
└── env/
    ├── environment.txt                   # 依赖记录
    └── test_gptsapi.py                   # GPTSAPI 连通性测试
```

## 环境要求

- Windows
- Python 3.11 或兼容版本
- GPTSAPI API Key

安装依赖：

```powershell
pip install PyQt6 openai
```

## API Key 配置

程序会读取环境变量 `GPTSAPI_API_KEY`。可以任选一种方式配置：

1. 在系统环境变量中设置 `GPTSAPI_API_KEY`。
2. 在 SnipDo 的 PowerShell 脚本中填写 `$apiKey`。
3. 不预先设置，首次翻译或 OCR 时在弹窗中手动输入。

示例：

```powershell
$env:GPTSAPI_API_KEY = "你的 API Key"
pythonw.exe .\gemini_translate.pyw
```

## 直接运行

启动主窗口：

```powershell
pythonw.exe .\gemini_translate.pyw
```

传入待翻译文本：

```powershell
pythonw.exe .\gemini_translate.pyw "Hello, world."
```

从 UTF-8 文本文件读取输入：

```powershell
pythonw.exe .\gemini_translate.pyw --file "C:\path\to\input.txt"
```

对图片文件做 OCR 后翻译：

```powershell
pythonw.exe .\gemini_translate.pyw --image "C:\path\to\image.png"
```

如果图片是临时文件，希望 OCR 读取后删除：

```powershell
pythonw.exe .\gemini_translate.pyw --image "C:\path\to\image.png" --delete-after
```

## SnipDo 集成

1. 打开 `snipdo_script_powershell_code/snipdo_gemini.txt`。
2. 按本机环境修改以下变量：

```powershell
$pythonExe = "C:\Users\你的用户名\AppData\Local\Programs\Python\Python311\pythonw.exe"
$scriptPath = "D:\test\my_snipdo_translate\gemini_translate.pyw"
$apiKey = ""
```

3. 将脚本内容配置到 SnipDo 的脚本动作中。
4. 在任意应用中选中文本，通过 SnipDo 触发该动作即可翻译。

说明：

- `$apiKey` 留空时，程序会尝试读取系统环境变量；仍未设置时会弹窗要求输入。
- SnipDo 传入的文本会先写入临时 UTF-8 文件，再通过 `--file` 传给主程序，避免长文本或特殊字符在命令行参数中丢失。
- 主程序使用 `pythonw.exe`，不会弹出控制台窗口。

## 使用说明

- 手动模式：打开主窗口，在上方文本框输入内容，点击 `Translate` 或按 `Ctrl + Enter`。
- 模式切换：点击 `模式` 可在 Auto、Translate、Dictionary 之间切换。
- 语言选择：通过窗口右上角的语言下拉框指定原文语言和目标语言。
- OCR：复制图片到剪贴板后，点击窗口中的 `OCR`，或通过托盘菜单选择 `OCR 剪贴板图片`。
- 复制结果：翻译或查词完成后点击 `Copy`。
- 隐藏窗口：点击 `Hide` 或关闭窗口会隐藏到托盘；要完全退出请使用托盘菜单的 `彻底退出`。

## 调试

调试日志会写入系统临时目录：

```text
%TEMP%\gemini_translate_debug.log
```

可用于排查 API Key、SnipDo 调用、单实例通信、OCR 和翻译请求问题。

## 备注

- 当前模型名在 `gemini_translate.pyw` 中配置为 `gpt-5.4-nano`。
- API 请求使用 GPTSAPI 兼容接口：`https://api.gptsapi.net/v1`。
- `legacy/` 目录保留了旧版实现，日常使用推荐运行 `gemini_translate.pyw`。
