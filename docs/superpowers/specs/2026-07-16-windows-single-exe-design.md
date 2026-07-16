# SnipDo Translate Windows 单文件 EXE 设计

日期：2026-07-16  
状态：已由用户逐节批准，等待书面复核  
目标平台：Windows 10/11 x64

## 背景

当前程序以 `gemini_translate.pyw` 启动，依赖本机 Python、PyQt6、OpenAI Python SDK 和虚拟环境。用户希望得到单个 Windows EXE，使目标电脑无需安装 Python 或执行 `pip install`，同时保留主窗口、托盘、SnipDo 调用、文本翻译、查词和图片 OCR。

现有程序不能原样打成可靠的 PyInstaller onefile 制品，原因包括：

- API Key 和翻译历史写在 `__file__` 旁，onefile 中该位置是临时解包目录。
- `--file` 当前会无条件删除输入文件。
- Windows 下现有 `QLocalServer` 逻辑不能可靠保证单实例，且长文本通信没有完整分帧和接收确认。
- 次实例会在单实例判断前创建窗口、托盘和全局鼠标钩子。
- 日志会记录用户原文且不会轮转。
- `legacy` 中存在已失效但曾被 Git 跟踪的硬编码凭据。
- 项目缺少自动化测试、PyInstaller spec 和可重复构建脚本。

## 目标

1. 交付一个 `SnipDoTranslate.exe`，在 Windows 10/11 x64 上运行时不依赖目标机安装 Python、PyQt6 或 OpenAI SDK。
2. 维持现有 UI 和翻译/OCR 行为，不改变 API 端点、模型或提示词业务逻辑。
3. 可靠支持直接启动和 SnipDo 的文本、文件、图片调用。
4. 将只读打包资源与可写用户数据彻底分离。
5. 首次运行时迁移现有 API Key 和翻译历史，只复制、不删除旧文件、不覆盖新数据。
6. 不把 API Key、翻译历史、日志、虚拟环境、诊断文件或 `legacy` 打进 EXE。
7. 使用离线自动测试、onedir 预检和 onefile 自检验证制品。

## 非目标

- 不重写翻译 UI、提示词、模型选择或 API 协议。
- 不支持 macOS、Linux、32 位 Windows 或 ARM Windows。
- 不制作 MSI/安装向导，也不实现便携模式。
- 不执行真实 API 自动测试，不发送用户内容，不产生测试 API 费用。
- 不重写 Git 历史；只清除当前工作树中已失效的硬编码凭据。
- 不提供 Authenticode 签名，因为当前没有代码签名证书。

## 选择的方案

采用“可靠安全版 PyInstaller”方案：先重构运行时边界和测试，再以 onedir 构建定位依赖问题，最后生成 onefile EXE。

未采用的方案：

- 最小 PyInstaller 打包：改动少，但会保留文件误删和单实例丢请求风险。
- Nuitka：也能生成单文件，但构建链更复杂，当前项目没有足够收益抵消迁移和验证成本。

## 模块边界

### `gemini_translate.pyw`

保留现有窗口、托盘、翻译线程、OCR、查词和交互行为。它消费结构化启动请求，不再自行拼接数据路径或解析原始命令行。

### `app_paths.py`

负责：

- 区分只读 bundle 目录和可写用户数据目录。
- 解析打包资源路径。
- 创建 `%LOCALAPPDATA%\SnipDoTranslate` 及日志目录。
- 发现旧版数据文件并执行幂等迁移。
- 验证、加载和原子保存翻译历史。

### `credential_store.py`

通过 `ctypes` 调用 Windows Credential Manager 原生 API，不增加运行时第三方依赖。固定凭据目标名为 `SnipDoTranslate/GPTSAPI`，负责读取、写入和读回验证。模块接口不向日志返回凭据内容。

### `app_cli.py`

使用 `argparse` 将命令行转换为不可歧义的 `AppRequest`。支持：

- 无参数或 `--show`
- 位置参数文本
- `--file PATH`
- `--image PATH`
- 与文件或图片配套的 `--delete-after`
- 内部构建验证使用的 `--self-test REPORT_PATH`

### `single_instance.py`

负责 Windows 用户会话内的单实例所有权和 Qt 本地通信：

- 使用 `Local\SnipDoTranslate-v2` 命名互斥锁决定主实例。
- 仅主实例创建 `QLocalServer`。
- 服务端使用当前用户访问选项。
- IPC 使用带长度的 UTF-8 JSON 帧和对应 ACK。
- 主 UI 尚未就绪时短暂排队已验证的请求。

### 构建与测试文件

- `SnipDoTranslate.spec`：显式描述 onefile 输入和唯一所需图标资源。
- `build_exe.ps1`：清理旧构建、运行测试、构建 onedir、执行自检、构建 onefile、再次自检并计算 SHA-256。
- `requirements.txt`：锁定经过验证的运行时依赖版本。
- `requirements-build.txt`：锁定经过验证的 PyInstaller、pytest 和图标转换依赖。
- `tests/`：纯逻辑、迁移、凭据、IPC、CLI 和构建安全测试。

## 启动与请求数据流

1. `app_cli.py` 解析参数并创建 `AppRequest`，但不删除任何输入文件。
2. 程序创建最小 `QApplication`，并设置稳定的应用名称。
3. `single_instance.py` 获取用户会话互斥锁。
4. 如果是次实例：
   - 文本文件先被完整读取到内存。
   - 图片路径交给主实例，由主实例成功载入图片后再确认。
   - 次实例最多短暂重试主服务，发送完整请求并等待匹配 request ID 的 ACK。
   - 只有主实例已安全接收文本，或已成功载入图片后，次实例才根据 `--delete-after` 删除源文件。
   - 收到 ACK 后次实例退出，不创建窗口、托盘或鼠标钩子。
5. 如果是主实例：
   - 创建本地服务并准备请求队列。
   - 解析用户数据目录并执行一次幂等迁移。
   - 创建窗口、托盘和鼠标钩子。
   - 处理初始请求和队列请求。

无参数的第二次启动等价于 `--show`，必须唤醒已有窗口并快速退出。

## IPC 协议

每个消息使用 4 字节大端无符号长度加 JSON UTF-8 载荷。单条消息上限为 16 MiB，超限时拒绝并返回错误，不分配不受限内存。

请求字段：

```json
{
  "version": 1,
  "id": "UUID",
  "action": "show | translate_text | ocr_image",
  "payload": {}
}
```

ACK 字段：

```json
{
  "version": 1,
  "id": "与请求相同的 UUID",
  "status": "accepted | error",
  "message": "不含用户正文的诊断信息"
}
```

服务端必须累积足够字节后再解码，不得在第一次 `readyRead` 时假设消息完整。次实例在主实例刚获得互斥锁但服务尚未监听时，以短间隔重试，总等待上限为 5 秒。

## 用户数据与迁移

### 新位置

```text
%LOCALAPPDATA%\SnipDoTranslate\
  translation_history.json
  logs\gemini_translate.log
```

API Key 不写入此目录，而是保存在 Windows Credential Manager。

### 旧数据候选目录

迁移只检查有限、明确的目录，不扫描磁盘：

1. 源码模式下的 `gemini_translate.pyw` 所在目录。
2. 打包模式下的 EXE 所在目录。
3. 仅当 EXE 的父目录包含 `gemini_translate.pyw` 时，检查该父目录；这覆盖项目 `dist` 目录中的首次本机构建。

### 迁移规则

- 新历史文件不存在且旧历史是 JSON 列表时，复制最多最新 50 项到新位置。
- 历史使用同目录临时文件、刷新、`fsync` 和 `os.replace` 原子写入。
- 新凭据不存在且发现非空旧 Key 时，将其写入 Credential Manager并读回验证。
- 任一迁移失败都保留旧文件，不覆盖已有新数据，不把正文或 Key 写入日志。
- 成功迁移也不删除旧文件；用户已明确要求只复制。
- 已失效的 `legacy` 硬编码凭据从当前工作树移除，但不作为迁移来源。

迁移在凭据解析前执行。API Key 的最终读取优先级：

1. 当前进程的 `GPTSAPI_API_KEY` 环境变量；仅本次运行使用，不持久化。
2. Windows Credential Manager。
3. 已成功写入并读回验证的旧文件迁移结果。
4. 密码掩码输入框。

## 文件删除语义

- `--file PATH` 和 `--image PATH` 默认永不删除输入。
- `--delete-after` 只允许与 `--file` 或 `--image` 一起使用。
- 文本文件只有在内容已完整进入主实例内存后才可删除。
- 图片只有在主实例已成功读取图片数据后才可删除。
- 连接失败、读取失败、解析失败或主实例拒绝请求时保留输入文件。
- SnipDo 创建的临时文本文件使用 `--file PATH --delete-after`。

## 日志与隐私

日志写入 `%LOCALAPPDATA%\SnipDoTranslate\logs`，单文件最大 1 MiB，保留 3 个备份。日志可以记录时间、request ID、动作类型、输入长度、状态、耗时和异常类型，但不得记录：

- 原文或译文
- 图片字节或 base64
- 完整文件内容
- API Key 或认证请求头

实施时要审计现有所有包含正文的 `log(...)` 调用，不能只依赖末端字符串过滤。

## 错误处理

- 参数错误、文件不存在、用户目录不可写等启动错误使用 Qt 对话框显示，因为最终 EXE 无控制台。
- Credential Manager 写入失败时允许本次会话继续使用内存中的 Key，但明确提示未能保存；旧 Key 文件不删除。
- 本地通信连接或 ACK 超时时显示明确错误，SnipDo 请求不得静默丢失。
- 损坏的旧历史不迁移，损坏的新历史不被空列表自动覆盖。
- 翻译 API 的超时、鉴权和服务端错误继续由现有 UI 显示，并与启动/打包错误分开记录。
- 资源缺失在 `--self-test` 和正常启动日志中都应被识别，不能仅静默退回系统图标。

## PyInstaller 设计

构建只显式加入：

- `gemini_translate.pyw` 及新运行时模块
- `snipdo_script_logo/gemini-color.png`
- 从现有 PNG 生成的多尺寸 Windows `.ico`
- PyInstaller 依赖分析确认必需的 Python/Qt/OpenAI 模块

不得使用把整个项目目录加入数据文件的通配配置。spec 明确排除：

- `.gptsapi_api_key`
- `translation_history.json`
- `.env*` 和日志
- `.venv`
- `legacy`
- `diagnostics`
- Git 元数据和本地编辑器配置

先生成 onedir 制品并检查 Qt 插件、原生扩展和 PyInstaller 警告；通过后才生成 onefile。最终 EXE 使用 windowed 模式，不弹出控制台。

## 自动化测试

实施遵循测试驱动开发，先建立会失败的测试，再写最小实现。至少覆盖：

- CLI：所有合法入口、缺参数、未知参数、Unicode/空格路径和 `--delete-after` 约束。
- 路径：源码模式、模拟 frozen 模式、bundle 只读资源和 LocalAppData 可写数据分离。
- 迁移：目标缺失时迁移一次、目标存在时不覆盖、损坏 JSON、超过 50 项、写入失败时保留旧数据。
- 凭据：环境变量优先、Credential Manager mock、旧 Key 导入、写入失败不删除旧文件、日志不含 Key。
- IPC：分片帧、多帧、非法长度、超限消息、Unicode、长文本、request ID 和 ACK 匹配。
- 删除：默认保留，只有成功接收且显式请求时删除；所有失败路径保留。
- 日志：不包含输入正文、译文或 Key，且轮转配置正确。
- 历史：原子保存和损坏文件保护。

API 调用使用 mock，不读取本地真实 Key，也不联网。

## 制品自检与烟测

`--self-test REPORT_PATH` 不显示主窗口、不安装全局鼠标钩子、不调用 API。它创建最小 Qt 应用并检查：

- PyQt6 和 Qt platform plugin 可以加载。
- 打包图标存在且可读取。
- 用户数据目录可以创建和写入临时探针文件，随后清理。
- QtNetwork 和 IPC 帧编解码组件可初始化。
- 报告以 JSON 写入指定路径，进程以 0/非 0 表示通过/失败。

构建脚本执行顺序：

1. 运行全部离线测试。
2. 构建 onedir。
3. 对 onedir EXE 执行 `--self-test`。
4. 构建 onefile。
5. 对 onefile EXE 执行 `--self-test`。
6. 运行单实例和命令行本地烟测。
7. 检查 PyInstaller archive 和制品字符串，不得包含本地 Key、历史、日志或排除目录。
8. 计算最终 EXE 的 SHA-256。

当前环境没有独立的干净 Windows 10/11 虚拟机，因此本次能完成本机 x64 构建与自检；“未安装 Python 的另一台电脑”仍需最终人工复制验证。这一限制必须写入构建报告，不得把本机自检描述成跨机器认证。

## SnipDo 集成

SnipDo PowerShell 脚本改为直接调用 EXE，不再引用 `pythonw.exe`、`.pyw` 或在脚本中保存 API Key。文本通过临时 UTF-8 文件传递：

```powershell
SnipDoTranslate.exe --file <temporary-file> --delete-after
```

如果 PowerShell 连 EXE 进程都未能创建，脚本负责清理临时文件；进程已创建后的正常路径由应用在收到安全 ACK 后删除。

## 交付物

- `dist/SnipDoTranslate.exe`
- `dist/SnipDoTranslate.exe.sha256`
- 本地构建与自检 JSON/文本报告
- `SnipDoTranslate.spec`
- `build_exe.ps1`
- 更新后的 SnipDo PowerShell 脚本
- 更新后的 README、运行时依赖锁和构建依赖锁
- 自动化测试

构建报告应记录 Git 提交、Python/PyQt6/OpenAI/PyInstaller 版本、测试结果、EXE 大小、SHA-256、已知的无签名 SmartScreen 风险和缺少独立干净 Windows 验证环境的限制。

## 验收条件

1. 自动化测试全部通过。
2. onedir 和 onefile 的 `--self-test` 均通过。
3. 最终 EXE 启动无控制台闪现，窗口、托盘和品牌图标可用。
4. 连续两次启动只产生一个功能实例和一个托盘实例；第二次请求被已有实例接收。
5. SnipDo 能传递 Unicode 和长文本，且临时文件只在安全接收后删除。
6. API Key 和历史跨重启保留在新的用户级存储中，旧文件不被删除。
7. EXE 和 archive 检查不包含真实 Key、历史、日志、`legacy`、诊断脚本或虚拟环境。
8. 最终 EXE、SHA-256 和构建报告均存在。
