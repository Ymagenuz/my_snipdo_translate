# SnipDo Translate

SnipDoTranslate 是面向 Windows 10/11 x64 的桌面翻译、查词和图片 OCR 工具。正式交付物是单文件 `SnipDoTranslate.exe`；目标电脑不需要安装 Python、PyQt6、OpenAI SDK 或项目依赖。

## 安装与首次启动

1. 将 `SnipDoTranslate.exe` 复制到一个固定目录，例如 `C:\Tools\SnipDoTranslate\`。
2. 双击 EXE，或在 PowerShell 中运行：

   ```powershell
   & "C:\Tools\SnipDoTranslate\SnipDoTranslate.exe"
   ```

3. 第一次执行需要联网的翻译或 OCR 时，程序会提示输入所选接口的 API Key。Key 会按接口分别保存到当前 Windows 用户的“凭据管理器”；不会写入明文配置文件或进程环境变量。

单文件 EXE 第一次启动可能比后续启动稍慢。程序使用单实例模式；再次运行时，请求会转发给已经运行的实例。

`start_snipdo_translate.cmd` 是可选启动器。把它与 `SnipDoTranslate.exe` 放在同一目录即可使用，它会原样转发所有命令行参数，不包含源码运行回退。

## Windows SmartScreen

当前 EXE 没有 Authenticode 代码签名，因此 Windows SmartScreen 可能显示“Windows 已保护你的电脑”。请先确认文件来自可信渠道并核对 SHA-256；确认无误后，可在提示中选择“更多信息”再选择“仍要运行”。不要对哈希不匹配或来源不明的文件绕过警告。

## 基本使用

- 在主窗口输入或粘贴文本后翻译；Auto 模式会在本地判断中译英或其他语言译中文，不再额外请求模型识别方向。
- Auto 模式会在本地识别单词、短语和简短术语并直接进入词典；完整句子仍按翻译处理，也可显式切换到词典模式强制查词。
- 词典结果使用分层 Markdown：词条标题下集中显示语言和读音，并以独立章节区分对应表达、释义、用法和双语例句。
- 从 SnipDo 触发后会立即打开结果窗口并流式显示内容；完整响应结束后再按 Markdown 约束统一渲染最终结果。
- 将图片复制到剪贴板后使用 OCR，或通过命令行传入图片文件。
- 点击主窗口右上角显示当前快捷键的按钮，或使用托盘菜单中的“设置…”，可以启用/禁用全局划词翻译、录入新的翻译快捷键、选择 API 接口及更新对应的 API Key。默认快捷键为 `XButton1`。
- 托盘图标使用带白色 `A`/`文` 的双向箭头，并以相同轮廓表达状态：蓝青色表示快捷键已启用并激活，灰色表示已禁用或未激活。
- 托盘右键菜单中的“禁用”是可勾选项：勾选后立即禁用全局划词翻译，取消勾选后立即重新启用，无需打开设置窗口。
- 鼠标快捷键会拦截对应的 XButton1/XButton2/中键原生动作，避免同时触发浏览器后退、前进或中键功能。低级鼠标回调运行在独立的 Win32 消息线程中，只投递翻译信号，不在回调内执行剪贴板、界面或网络工作。
- 关闭主窗口通常只会隐藏到系统托盘；要完全退出，请使用托盘菜单中的退出命令。

设置中的“API 接口”目前提供：

- `OpenAI 兼容（GPTSAPI）`：保持原有行为，使用 `https://api.gptsapi.net/v1` 和 `gpt-5.4-nano`；可选环境变量为 `GPTSAPI_API_KEY`，凭据目标为 `SnipDoTranslate/GPTSAPI`。
- `DeepSeek 官方接口`：使用 `https://api.deepseek.com` 和 `deepseek-v4-flash`；可选环境变量为 `DEEPSEEK_API_KEY`，凭据目标为 `SnipDoTranslate/DeepSeek`。该接口当前用于文本翻译、查词和文本对齐，不支持本程序的图片 OCR；执行 OCR 前程序会明确拒绝并保留传入的源文件。

两种接口都通过 OpenAI Chat Completions 兼容格式调用，但服务地址、模型和凭据相互独立。切换接口时，API Key 输入框留空会尝试使用目标接口已有的环境变量或 Windows 凭据；不存在时会在下一次请求前提示输入。

常用命令行入口：

```powershell
# 显示窗口；不带参数时效果相同
& .\SnipDoTranslate.exe --show

# 直接传入文本
& .\SnipDoTranslate.exe "Hello, world."

# 读取 UTF-8 文本文件
& .\SnipDoTranslate.exe --file "C:\路径 含空格\input.txt"

# 对图片执行 OCR 后翻译
& .\SnipDoTranslate.exe --image "C:\路径 含空格\image.png"
```

`--file` 和 `--image` 默认保留源文件。只有确实需要处理临时文件时才添加 `--delete-after`：

```powershell
& .\SnipDoTranslate.exe --file "C:\Temp\selection.txt" --delete-after
& .\SnipDoTranslate.exe --image "C:\Temp\capture.png" --delete-after
```

删除采用安全确认语义：当前实例必须真正接收并拥有数据，或已运行的主实例必须返回明确的 `accepted` ACK，程序才会删除源文件。OCR 正忙、缺少或取消输入 Key、文件读取失败、启动失败、IPC 超时或请求被拒绝时，源文件会保留。`--delete-after` 不能单独使用。

## SnipDo 集成

1. 打开 `snipdo_script_powershell_code\snipdo_translate.txt`。
2. 只修改脚本第一处 `$exePath`，使其指向你实际存放的 `SnipDoTranslate.exe`。路径可以包含空格。
3. 将完整脚本复制到 SnipDo 的 PowerShell 动作中。
4. 在任意应用中选中文本并触发该动作。

该脚本兼容 Windows PowerShell 5.1。选中文本会写入无 BOM 的 UTF-8 临时文件，再以 `--file <临时文件> --delete-after` 启动 EXE。脚本自身不保存 Key，也不设置环境变量；若 EXE 未能启动，脚本不会主动删除临时文件，以便尽可能保留原始输入。

## 用户数据与隐私

所有可写运行数据位于当前用户目录：

```text
%LOCALAPPDATA%\SnipDoTranslate\
  settings.json
  translation_history.json
  logs\
    SnipDoTranslate.log
    SnipDoTranslate.log.1 ... .3
```

- `translation_history.json` 保存最多 50 条历史记录，可能包含原文和译文，请按敏感用户数据对待。
- `settings.json` 只保存工具启用状态、快捷键和所选 API 接口，不保存 API Key。
- 日志是固定事件组成的 UTF-8 JSON 行，不记录原文、译文、OCR 内容、API Key、完整路径或异常正文。
- 日志单文件最多 512 KiB，并保留 3 个轮转备份，总上限约 2 MiB。
- API Key 不在上述目录中，而是在 Windows Credential Manager 中按当前用户、按接口分别保存。

发现旧版 `translation_history.json` 或 `.gptsapi_api_key` 时，程序只在新目标不存在时执行复制迁移。迁移不会删除、覆盖或改写旧文件，也不会用旧数据覆盖已经存在的新历史或凭据。

## 离线自检

以下自检不读取真实 Key，也不会发出翻译 API 请求：

```powershell
& .\SnipDoTranslate.exe --self-test offline
if ($LASTEXITCODE -ne 0) { throw "SnipDoTranslate 离线自检失败" }
```

自检会验证 Windows x64 运行环境、打包资源、本地数据目录写入和本地 IPC 编解码。成功时退出码为 `0`，失败时为非零。

## SHA-256 验证

交付目录同时包含 `SnipDoTranslate.exe.sha256`。可用 Windows PowerShell 核对：

```powershell
$expected = ((Get-Content -LiteralPath .\SnipDoTranslate.exe.sha256 -TotalCount 1) -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath .\SnipDoTranslate.exe).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 不一致，请勿运行该文件" }
"SHA-256 验证通过：$actual"
```

## 开发与构建

最终用户不需要本节中的工具。构建电脑需要 Windows 10/11 x64、64 位 Python 和项目构建依赖；整个自动测试流程保持离线，不使用真实 Key，也不调用翻译 API。

在本 worktree 根目录准备 `.venv` 后运行：

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
& .\.venv\Scripts\python.exe -m pip install pytest pyinstaller
PowerShell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

构建脚本严格按以下顺序执行：

1. 运行全部离线测试并确认构建 Python 为 x64。
2. 构建 PyInstaller `onedir` 预检产物。
3. 检查 onedir 的 PE x64 格式并运行 `--self-test offline`。
4. 预检通过后才构建最终 `onefile` EXE。
5. 检查 onefile 的 PE x64 格式、未签名状态和离线自检，并生成 SHA-256 文件。

最终产物位于：

```text
dist\SnipDoTranslate.exe
dist\SnipDoTranslate.exe.sha256
```

本机构建和自检不能替代在一台未安装 Python 的干净 Windows 10/11 x64 电脑上做最终人工烟测。
