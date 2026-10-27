# SnipDo Translate

SnipDoTranslate 是面向 Windows 10/11 x64 的桌面翻译、查词和图片 OCR 工具。正式交付物是单文件 `SnipDoTranslate.exe`；目标电脑不需要安装 Python、PyQt6、OpenAI SDK 或项目依赖。

## 版本与应用内更新

- 应用版本统一定义在 `app_version.py`，当前版本为 `12.3.2`：缩短翻译等待时间——OpenRouter 接口的“禁用思考”改为发送其 `reasoning` 参数；每次翻译后 15 分钟内保持到接口的 HTTPS 连接，后续翻译无需重新握手；原文含数学公式时在后台预先加载公式排版组件，避免结果显示时界面卡顿。翻译和词典结果在流式接收时实时按 Markdown 渲染，不再随输出向下滚动，响应结束时也保持当前滚动位置。主窗口标题、设置标题、托盘“关于”菜单及打包 EXE 的 Windows 文件属性均显示版本。
- 正常启动主实例 5 秒后，程序会在后台检查本项目的 [GitHub Releases](https://github.com/Ymagenuz/my_snipdo_translate/releases)。自动检查每台设备最多每天一次；保持运行时也会每天检查。托盘菜单中的“检查更新…”可随时手动检查。
- 只有正式版版本号更高、且已上传有效的 `SnipDoTranslate.exe` 和 `SnipDoTranslate.exe.sha256` 附件时才会提示。草稿、预发布版本、只有源码的 Release、缺少校验文件和旧版本均不会提示。
- 提醒提供“更新并重启”“稍后”和“跳过此版本”。点击“更新并重启”后，程序直接下载更新并显示进度，无需打开浏览器。“稍后”在本次运行期间不重复提醒同一版本；“跳过此版本”会在本机保存，手动检查仍可查看被跳过的版本。
- 下载期间可继续使用或取消；程序核对文件大小和 SHA-256 后，等待翻译、OCR 与设置完成，再退出、替换原位置的 EXE 并重新打开。设置、历史记录、API Key 和开机启动路径会保留。下载、校验或准备失败时保持当前版本运行，可通过“检查更新…”重试；安装或新版启动失败时尝试恢复旧 EXE 并重新启动，保留恢复文件以便排查。
- 应用内安装适用于可写目录中的正式单文件 Windows EXE；源码运行和开发用 onedir 包会给出说明，不会替换 Python 或开发环境。更新只在点击按钮后下载和安装，不请求管理员权限。程序目录不可写时，请先将单文件 EXE 放到当前用户可写的固定目录。
- 检查失败或设备离线时，后台检查保持安静；手动检查会显示失败提示。网络检查异步执行且有超时，不阻塞翻译。检查只向 GitHub 请求公开的发行信息，不发送原文、译文、历史记录或 API Key；请求的 User-Agent 包含应用版本。
- 已安装的旧 EXE 不会因代码推送而改变。旧版只有更新提醒功能时，需要先通过原有方式安装一次 `12.3.0` 或更新版本；之后的正式更新可直接在应用内完成。
- `12.0.0` / `12.1.0` 的更新检查存在异常退出问题，请直接从 GitHub Releases 手动下载 `12.1.1` 或更新版本，退出旧程序后替换 EXE。

发布新版本时：

1. 修改 `app_version.py` 中的 `APP_VERSION`，采用 `主版本.次版本.修订版本` 格式，例如 `12.3.2`，每段为 `0`–`65535` 的整数。
2. 运行下方 Windows 构建流程，得到已校验的 `dist\SnipDoTranslate.exe` 和 SHA-256 文件；构建检查会验证 EXE 中的版本资源与源码一致。
3. 在本项目 GitHub Releases 中创建对应标签（例如 `v12.3.2`）的正式 Release，上传 `SnipDoTranslate.exe` 和 `SnipDoTranslate.exe.sha256`，填写更新说明，再发布并标记为最新版本。校验文件使用构建脚本生成的 `SHA256  SnipDoTranslate.exe` 格式。只推送代码或创建 Git 标签不会发布应用更新。

所有设备共用这个公开的发布源，无需搭建推送服务器或在设备间直接连接。EXE 内置固定的本项目检查地址；无需 GitHub Token。

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
- 触发翻译或查词后会立即打开结果窗口，并在流式接收时实时按 Markdown 渲染内容。翻译和词典模式都停留在结果开头、不随输出向下滚动；手动滚动后保持当前位置，响应结束时也不会跳回顶部。流式期间公式先显示为 LaTeX 源码，完整响应结束后再统一排版为最终结果。
- 支持 LaTeX 文档和数学公式：保留命令、环境、花括号、换行、公式、注释、引用键及标签，只翻译正文、标题和文本参数中的自然语言；译文中的 `$...$`、`\(...\)`、`$$...$$`、`\[...\]` 及常见数学环境会离线排版显示，复制时仍保留原始 LaTeX，可通过 `--file` 翻译 UTF-8 `.tex` 文件。
- 主窗口不再根据文本长度或模式自适应高度；非最大化状态固定使用 `560 × 700` 的大窗口，最大化状态不会被强制缩放。
- 翻译完成时，如果主窗口已失去焦点，Windows 会弹出正文为译文内容的完成通知，托盘及窗口/任务栏图标右上角会显示红色未读标志；点击通知、点击带未读标志的托盘图标或重新聚焦窗口后会自动清除。
- 将图片复制到剪贴板后使用 OCR，或通过命令行传入图片文件。
- 点击主窗口右上角的齿轮按钮，或使用托盘菜单中的“设置…”，可以启用/禁用全局划词翻译、录入新的翻译快捷键和显示窗口快捷键，以及配置 API 接口、服务地址、模型和对应的 API Key。悬停齿轮按钮可查看当前翻译快捷键。默认翻译快捷键为 `XButton1`，默认显示窗口快捷键为 `Ctrl+Alt+W`。
- 翻译快捷键和显示窗口快捷键都支持键盘组合键、`XButton1`、`XButton2` 与中键，但两者不能设置为同一个按键。点击显示窗口快捷键进入录入状态后，按 `Esc` 可移除该快捷键，显示为“未设置”；保存后生效，重启后仍保持移除状态。翻译快捷键不可移除，录入时按 `Esc` 只取消修改并保留原按键。取消整个设置对话框不会保存改动。
- 设置中的“开机启动”提供“登录 Windows 时自动启动（后台托盘）”选项，默认关闭。勾选并保存后，下次当前用户登录 Windows 时会自动启动，驻留系统托盘且不弹出主窗口；取消勾选并保存即可关闭。已运行时不会重复启动或打断当前窗口。此选项无需管理员权限，独立于全局划词翻译的启用状态。
- 为减少等待，每次翻译后的 15 分钟内，程序会在连接空闲约 90 秒时于后台刷新到所选接口服务地址的 HTTPS 连接，使后续翻译可复用已建立的连接，省去重新握手的时间。刷新只向服务地址发送不含正文的 `HEAD` 请求，不携带 API Key、原文或译文；空闲超过 15 分钟后停止刷新。
- 原文含数学公式时，程序会在翻译开始时于后台加载公式排版组件，避免结果显示时界面短暂卡顿。
- 划词翻译会等待快捷键及 Ctrl/Alt/Shift/Win 等按键松开后再复制选中文字；请在原窗口中选中文本，按下并松开快捷键。等待超过 2 秒会提示重试，期间切换窗口或禁用/修改翻译快捷键会取消本次取词。复制最多等待 1 秒，以兼容响应较慢的应用，并在结束后恢复原剪贴板。原应用仍需允许复制文本；图片和扫描件请使用 OCR。
- 即使全局划词翻译已禁用，已配置的显示窗口快捷键仍然有效；窗口隐藏或最小化时会恢复窗口，窗口已经显示但失焦时会将它置前并切换为焦点窗口，不会清空当前内容、改变最大化状态或取消正在进行的任务。移除该快捷键后，仍可通过托盘菜单“显示主窗口”打开窗口。
- 托盘图标使用带白色 `A`/`文` 的双向箭头，并以相同轮廓表达状态：蓝青色表示快捷键已启用并激活，灰色表示已禁用或未激活。
- 托盘右键菜单中的“禁用”是可勾选项：勾选后立即禁用全局划词翻译，取消勾选后立即重新启用，无需打开设置窗口。
- 鼠标快捷键会拦截对应的 XButton1/XButton2/中键原生动作，避免同时触发浏览器后退、前进或中键功能。低级鼠标回调运行在独立的 Win32 消息线程中，只投递翻译信号，不在回调内执行剪贴板、界面或网络工作。
- 关闭主窗口通常只会隐藏到系统托盘；要完全退出，请使用托盘菜单中的退出命令。

设置中的“API 接口”提供四个独立配置。所有配置都使用 OpenAI Chat Completions 兼容格式，并允许编辑模型；只有“OpenAI 兼容接口（自定义）”允许编辑服务地址（Base URL），其他接口的服务地址为只读：

| 接口 | 初始 Base URL | 预设模型 | 可选环境变量 | Windows 凭据目标 |
| --- | --- | --- | --- | --- |
| GPTSAPI | `https://api.gptsapi.net/v1` | `gpt-5.4-nano` | `GPTSAPI_API_KEY` | `SnipDoTranslate/GPTSAPI` |
| OpenAI 兼容 | `https://api.openai.com/v1` | 留空，由用户选择或输入 | `OPENAI_API_KEY` | `SnipDoTranslate/OpenAICompatible` |
| DeepSeek 官方接口 | `https://api.deepseek.com` | `deepseek-v4-flash` | `DEEPSEEK_API_KEY` | `SnipDoTranslate/DeepSeek` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-5.6-luna` | `OPENROUTER_API_KEY` | `SnipDoTranslate/OpenRouter` |

`OpenAI 兼容` 是独立配置，可以填写 OpenAI 或其他兼容服务的地址和模型。GPTSAPI 保留原有默认配置与凭据，新安装默认仍选择 GPTSAPI。表中的模型 ID 只是应用预设；实际可用模型取决于所选服务及账号权限。

配置或切换模型：

1. 打开设置并选择接口，填写该服务的 API Key。使用“OpenAI 兼容接口（自定义）”时可以编辑 Base URL；其他接口的地址只读。Base URL 应为服务提供的 API 根地址，不包含 `/chat/completions` 或 `/models`。
2. 点击“获取模型”，程序会在后台请求当前填写地址的 `/models`，优先使用当前输入的 Key；留空时使用所选接口已有的环境变量或 Windows 凭据。
3. 获取模型后，可在模型下拉框中输入关键词搜索，按模型 ID 包含的文字匹配且不区分大小写；也可以直接选择模型或输入完整模型 ID。服务不支持模型列表或获取失败时，仍可手动输入并保存。
4. 根据所选模型配置图片输入/OCR 能力。只有服务和模型实际支持图片输入时才启用；关闭时，程序会在执行 OCR 前拒绝请求并保留传入的源文件。DeepSeek 预设关闭图片输入。
5. 按需设置“禁用思考”，然后保存。OpenRouter 接口会在请求中加入 `reasoning: {"effort": "none"}`，其他接口加入 `thinking: {"type": "disabled"}` 扩展，仅适用于支持此参数的服务和模型；DeepSeek 预设启用。关闭思考通常能明显缩短首字等待时间；若所选模型要求必须推理并返回错误，请取消勾选。

各接口分别保留服务地址、模型和能力选项，切换接口及重启后仍使用已保存的配置；更换模型无需重新打包程序。API Key 也按接口独立保存，输入框留空不会清除已有凭据；未提供 Key 且没有可用凭据时，程序会在需要请求时提示输入。获取模型只读取列表，不会自动保存当前输入的 Key。

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

开机启动使用 `--startup` 参数静默进入系统托盘，不会在登录时请求输入 API Key；双击 EXE 或使用 `--show` 仍正常显示窗口。启动项只保存在当前用户的 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 下，名称为 `SnipDoTranslate`，不会写入 `settings.json`。如果移动了 EXE，请从新位置运行程序并重新保存开机启动选项；如果在 Windows 任务管理器中禁用了该启动项，也需要在那里重新启用。源码模式会使用同目录的 `pythonw.exe` 与脚本绝对路径注册，文件需要保持在原位置。

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
  update_state.json
  logs\
    SnipDoTranslate.log
    SnipDoTranslate.log.1 ... .3
```

- `translation_history.json` 保存最多 50 条历史记录，可能包含原文和译文，请按敏感用户数据对待。
- `settings.json` 保存工具启用状态、翻译快捷键、显示窗口快捷键、所选 API 接口，以及各接口的服务地址、模型和能力选项，不保存 API Key；旧版配置会自动补入缺失的默认设置，并保留原有接口选择。
- `update_state.json` 只保存上次检查更新的时间和用户选择跳过的版本；删除后会恢复默认检查行为。
- 公式排版库可能创建只含字体索引的缓存，不包含原文或译文；单文件 EXE 使用退出时清理的隔离临时目录，源码模式可能使用 `%LOCALAPPDATA%\SnipDoTranslate\matplotlib-cache\`。
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

自检会验证 Windows x64 运行环境、打包资源、离线 LaTeX 公式排版、本地数据目录写入和本地 IPC 编解码。成功时退出码为 `0`，失败时为非零。

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
