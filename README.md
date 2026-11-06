# SnipDo Translate

**English** | [简体中文](README.zh-CN.md)

SnipDoTranslate is a desktop translation, dictionary and image OCR tool for Windows 10/11 x64. It ships as a single file, `SnipDoTranslate.exe`; the target computer doesn't need Python, PyQt6, the OpenAI SDK or any project dependency.

The interface is in English by default. To switch it to Simplified Chinese, open **Settings** → **General** → **Language**, choose **简体中文** and click **Save**; menus, buttons and messages switch right away. The interface language doesn't affect the translation direction or the target language.

## Versions and in-app updates

- The app version is defined once, in `app_version.py`. The current version is `12.7.0`, which makes scrolling glide in step with the monitor, fades parts of the interface in gently and gives scroll bars and dropdowns a slimmer, modern look; see the [changelog](CHANGELOG.md). The version appears in the main window title, the Settings title, the **About** page in Settings, the tray menu's **About** item and the packaged EXE's Windows file properties.
- 5 seconds after the main instance starts normally, the app checks this project's [GitHub Releases](https://github.com/Ymagenuz/my_snipdo_translate/releases) in the background. Automatic checks run at most once every 6 hours per device, and restarting the app doesn't trigger an early check unless the last check found a newer version, which is looked up again right away so the Settings button can show it; while the app keeps running it also checks every 6 hours. **Check for updates…** in the tray menu, or **Check for updates** on the **About** page in Settings, checks at any time.
- A version only counts as available when a full release has a higher version number and valid `SnipDoTranslate.exe` and `SnipDoTranslate.exe.sha256` assets. Drafts, pre-releases, source-only releases, releases without the checksum file and older versions are never shown.
- Updates never interrupt you with a pop-up. When a newer version is available, the gear on the **Settings** button in the main window turns into a blue download icon; hover over it to see the version, or click it to open the **About** page in Settings. The About page shows the installed version, the available version, **Update and restart**, **Release notes** and **Check for updates**, and the changelog, where the installed release is marked **Installed**; when an update is available, its release notes from GitHub appear at the top, marked **Available**. **Update and restart** saves any changes you made in Settings, closes it, then downloads the update inside the app and shows its progress, without opening a browser. **Release notes** opens that version's release page in your browser, where you can also download it yourself; if no browser can be opened, the page address is shown instead. The tray menu's **About** item opens the same page, and **Check for updates…** opens it and checks right away, showing the result there.
- You can keep working or cancel during the download. The app checks the file size and SHA-256, waits for translation, OCR and Settings to finish, then quits, replaces the EXE in place and starts it again. Settings, history, API keys and the startup entry are kept. If downloading, verifying or preparing fails, the current version keeps running and you can try again with **Check for updates…**. If installing or starting the new version fails, the app tries to restore the old EXE and start it again, keeping the recovery files for troubleshooting.
- In-app installation works for the official single-file Windows EXE in a writable folder. Running from source and the development onedir build show an explanation and never replace Python or the development environment. Updates are only downloaded and installed after you click the button, and never ask for administrator rights. If the app's folder isn't writable, first move the single-file EXE to a fixed folder the current user can write to.
- When a check fails or the device is offline, background checks stay silent; a manual check shows the failure on the **About** page. Checks run asynchronously with a timeout and never block translation. They only request public release information from GitHub and never send source text, translations, history or API keys; the request's User-Agent includes the app version.
- An installed older EXE doesn't change when code is pushed. If your version only has update notifications, install `12.3.0` or later once the way you installed it before; after that, releases can be installed from within the app.
- The update check in `12.0.0` / `12.1.0` can make the app exit unexpectedly. Download `12.1.1` or later from GitHub Releases yourself, quit the old app and replace the EXE.

### Publishing a release

1. Set `APP_VERSION` in `app_version.py` in `major.minor.patch` form, for example `12.7.0`; each part is an integer from `0` to `65535`. Add the release's entry to the top of both `CHANGELOG.md` and `CHANGELOG.zh-CN.md` (`## <version> — <YYYY-MM-DD>` followed by bullets); the app shows them on the About page, and the offline self-test fails if either one doesn't start with the new version.
2. Run the Windows build below to get the verified `dist\SnipDoTranslate.exe` and its SHA-256 file. The build checks that the EXE's version resource matches the source.
3. On this project's GitHub Releases page, create a full release for the matching tag (for example `v12.7.0`), upload `SnipDoTranslate.exe` and `SnipDoTranslate.exe.sha256`, write the release notes, then publish it and mark it as the latest release. The checksum file uses the `SHA256  SnipDoTranslate.exe` format the build script produces. Pushing code or creating a Git tag alone doesn't publish an app update.

Release titles and notes are written in English:

- **Title:** `SnipDo Translate v<version>`, for example `SnipDo Translate v12.7.0`.
- **`## What's changed`:** one bullet per user-visible change, saying what is different and why it matters.
- **`## Upgrading`:** how to upgrade (for example, from `12.3.0` or later choose **Update and restart** — in the update notification up to `12.5.0`, or on the **About** page in Settings from `12.6.0` on), that settings, history and API keys are kept, and whether the settings file format changed.
- **`## Download and verification`:** a bullet for each asset — `SnipDoTranslate.exe` (the Windows x64 single-file app; no Python or project dependencies needed) and `SnipDoTranslate.exe.sha256` (the EXE's SHA-256 checksum file) — then the EXE's SHA-256 in a `text` code block, a line giving the number of automated tests that passed, confirming the packaged app passed the offline self-test and naming the build version and commit, and finally a `[Full changelog](https://github.com/Ymagenuz/my_snipdo_translate/compare/v<previous>...v<version>)` link.

Releases up to `v12.4.1` were published with Chinese notes (headings 更新内容 / 升级 / 下载与校验, which correspond to the three headings above). Leave them as they are.

Every device uses this public release source, so there's no push server to run and no direct connection between devices. The EXE has this project's check address built in; no GitHub token is needed.

## Installation and first start

1. Copy `SnipDoTranslate.exe` to a fixed folder, for example `C:\Tools\SnipDoTranslate\`.
2. Double-click the EXE, or run it from PowerShell:

   ```powershell
   & "C:\Tools\SnipDoTranslate\SnipDoTranslate.exe"
   ```

3. The first time you run a translation or OCR that needs the network, the app asks for the selected provider's API key. Keys are stored per provider in the current Windows user's Credential Manager; they are never written to a plain-text config file or the process environment.

The single-file EXE may start a little slower the first time. The app runs as a single instance; starting it again forwards the request to the instance that is already running.

`start_snipdo_translate.cmd` is an optional launcher. Put it in the same folder as `SnipDoTranslate.exe`; it passes all command-line arguments through unchanged and has no fallback to running from source.

## Windows SmartScreen

The EXE currently has no Authenticode signature, so Windows SmartScreen may show "Windows protected your PC". First make sure the file came from a trusted source and check its SHA-256; once you're sure, choose **More info** and then **Run anyway**. Never bypass the warning for a file whose hash doesn't match or whose source you don't know.

## Basic use

- Type or paste text in the main window and translate it. Auto mode decides locally between Chinese → English and other languages → Chinese, without an extra model request to detect the direction.
- Auto mode recognizes single words, phrases and short terms locally and looks them up in the dictionary; full sentences are still translated. You can also switch explicitly to dictionary mode to force a lookup.
- Dictionary results use layered Markdown: language and pronunciation sit under the entry's title, and corresponding expressions, definitions, usage and bilingual examples each get their own section.
- The result window opens as soon as a translation or lookup starts and renders Markdown live while the response streams in. Both translation and dictionary mode stay at the top of the result instead of scrolling with the output; once you scroll yourself, your position is kept, and the view doesn't jump back to the top when the response finishes. While streaming, formulas appear as LaTeX source and are typeset once the full response has arrived.
- While a translation or lookup runs, a small **Stop** button appears beside the translation's heading (also in the selection-translation result window), and **Translate** stays in place showing **Translating...**. The translation fades in as it arrives: each new paragraph as a whole, and its characters in reading order; with **Animation effects** off in Windows, it appears at once. Clicking it stops receiving at once and keeps the translation received so far, with a grey **Stopped** note under it; you can copy it right away. If a translation fails, the text that arrived stays as it was and the error appears in a red note under it with **Try again**. Unfinished translations aren't saved to **History** and aren't back-translated. During a back-translation, the **Stop** link to the right of the **BACK TRANSLATION** heading stops just the back-translation.
- For long source texts or translations, rendering lays out the whole document only once, and a formula that appears several times in a translation is typeset only once; selecting text no longer re-analyzes the whole text. Long texts therefore stay smooth while streaming, when rendering finishes and while you drag a selection. Source text length isn't limited.
- LaTeX documents and math are supported: commands, environments, braces, line breaks, formulas, comments, citation keys and labels are kept, and only natural language in body text, headings and text arguments is translated. `$...$`, `\(...\)`, `$$...$$`, `\[...\]` and common math environments in translations are typeset offline; copying still keeps the original LaTeX. Use `--file` to translate a UTF-8 `.tex` file.
- Formulas match the surrounding text: inline formulas sit on the text baseline and are sized to the text's x-height. Display formulas use display style, fractions keep full-size numerators and denominators, and formulas that are too wide break at top-level `\quad` and `\qquad` instead of being scaled down. Subscripts and superscripts shrink level by level but never below 10 px, so nested ones stay readable. Display formulas leave no extra blank lines above or below.
- After a translation, select text in the source or the translation and click the floating **Locate in translation** / **Locate in original** button to highlight the corresponding text. This works for selection-translation results, manual translations and entries opened from **History**. Once you edit the source in the manual input box, locating is disabled until you translate again; dictionary results don't offer it. After locating, a single click anywhere in the source or translation clears the highlight (and cancels a locate that hasn't returned yet); scrolling either pane doesn't affect the highlight.
- Scrolling glides instead of jumping: the mouse wheel and clicks in a scroll bar move the source, translation, back-translation and the changelog in Settings smoothly, by the same distance as before. Each glide draws one frame per refresh of the monitor the window is on, for example 100 or 144 times a second, rather than on a fixed 60 Hz timer. In the read-only translation, back-translation and changelog, Page Up/Page Down, Space, Home/End and the arrow keys glide too, while in the input box keys still move the view with the text cursor at once. Locating glides to the highlighted text, the **Locate in translation** / **Locate in original** button and Settings pages fade in, and the previous translation dims gently when you click **Edit**. With **Animation effects** turned off in Windows (**Settings** → **Accessibility** → **Visual effects**), everything moves at once as before.
- Scroll bars in the source, translation, back-translation and changelog are slim rounded lines without arrow buttons that rest on the text box's own background. Pointing at one widens it smoothly over a faint track, and it settles back a moment after the pointer leaves. Dropdowns show a simple chevron that turns over while their list is open, and the lists use the same rounded rows and blue highlight as the **History** menu.
- Optional back-translation helps you check a translation. Turn it on with the **Back-translate** button next to the translation heading (or tick it on the **Translation** page in Settings). From then on, each finished translation is translated back into the source language and shown in the **BACK TRANSLATION** area below it. The back-translation request only sends the translation and the source language detected locally — never the source text — so the model can't simply copy the source. The source language is detected locally by default, or you can set it with the language selector. Entries opened from **History** are back-translated too while it's on; dictionary results, and translations in the same language as the source, aren't back-translated. Each back-translation sends one extra request. It's off by default and the setting is remembered.
- The main window no longer adjusts its height to the text length or mode. When not maximized it always uses a large `560 × 700` window, and a maximized window is never resized. After moving between monitors with different scaling, the window reopens at `560 × 700` in the center of the primary monitor; a window that was maximized before it was hidden reopens maximized.
- When a translation finishes while the main window isn't focused, Windows shows a completion notification containing the translation, and the tray, window and taskbar icons get a red unread badge in the top-right corner. Clicking the notification, clicking the badged tray icon or focusing the window again clears it. To turn off completion notifications (on by default), untick **Show a Windows notification when a translation finishes** on the **General** page in Settings; the unread badge still appears. Other notices, such as a shortcut that couldn't be registered or no selected text found, aren't affected.
- For OCR, copy an image to the clipboard and use **OCR**, or pass an image file on the command line.
- Open Settings with the gear button in the top-right corner of the main window or with **Settings…** in the tray menu. Settings is split into pages listed on the left: **General** (interface language, turning global selection translation on or off, startup, completion notifications), **Shortcuts** (translation shortcut and show-window shortcut), **Translation** (back-translation) and **API** (provider, base URL, model and the matching API key). **Save** applies the changes on every page at once; if the provider settings are invalid, Settings switches to the **API** page and shows why. Hover over the gear button to see the current translation shortcut. The default translation shortcut is the mouse back button (`XButton1`, shown as **Mouse back button**) and the default show-window shortcut is `Ctrl+Alt+W`.
- Both the translation shortcut and the show-window shortcut accept keyboard combinations, `XButton1`, `XButton2` and the middle mouse button, but they can't be the same. Click the show-window shortcut to start recording, then press `Esc` to remove it; it then shows **Not set**. The change takes effect when you save and stays removed after a restart. The translation shortcut can't be removed; pressing `Esc` while recording it only cancels the change and keeps the previous shortcut. Cancelling the Settings dialog saves nothing.
- **Startup** on the **General** page offers **Start when I sign in to Windows (in the tray)**, off by default. Tick it and save, and the app starts the next time the current user signs in to Windows, staying in the system tray without opening the main window; untick it and save to turn it off. An instance that is already running isn't started twice or interrupted. This option needs no administrator rights and is independent of whether global selection translation is on.
- To cut waiting time, for 15 minutes after each translation the app refreshes its HTTPS connection to the selected provider's base URL in the background whenever the connection has been idle for about 90 seconds, so the next translation can reuse it without a new handshake. The refresh only sends a `HEAD` request with no content to the base URL, never the API key, source text or translation, and stops after 15 idle minutes.
- When the source text contains math, the app loads the formula renderer in the background as the translation starts, so the interface doesn't stall briefly when the result appears.
- Selection translation waits until the shortcut and Ctrl/Alt/Shift/Win are released before copying the selected text: select text in the original window, then press and release the shortcut. After 2 seconds the app asks you to try again, and switching windows or turning off or changing the translation shortcut in the meantime cancels the capture. Copying waits up to 1 second for slow applications and restores the original clipboard afterwards. The original application must still allow copying text; use OCR for images and scans.
- The show-window shortcut keeps working even when global selection translation is off. If the window is hidden or minimized, it is restored; if it's visible but not focused, it's brought to the front and focused, without clearing its content, changing its maximized state or cancelling running work. Without that shortcut, **Show main window** in the tray menu still opens the window.
- The tray icon is a two-way arrow with a white `A`/`文`, and its color shows the state with the same outline: teal when the shortcut is on and active, gray when it's off or inactive.
- **Disable** in the tray menu is a checkable item: tick it to turn off global selection translation immediately, untick it to turn it back on, without opening Settings.
- Mouse shortcuts block the native XButton1/XButton2/middle-button action, so a browser doesn't also go back, forward or middle-click. The low-level mouse callback runs on its own Win32 message thread and only posts the translation signal; it never touches the clipboard, the interface or the network.
- Closing the main window normally just hides it in the system tray. To quit completely, use **Quit** in the tray menu.

The **API** page in Settings has four independent configurations. All of them use the OpenAI Chat Completions format and let you edit the model; only **OpenAI-compatible (custom)** lets you edit the base URL — the other providers' base URLs are read-only:

| Provider | Initial base URL | Preset model | Optional environment variable | Windows credential target |
| --- | --- | --- | --- | --- |
| GPTSAPI | `https://api.gptsapi.net/v1` | `gpt-5.4-nano` | `GPTSAPI_API_KEY` | `SnipDoTranslate/GPTSAPI` |
| OpenAI-compatible | `https://api.openai.com/v1` | Empty; you choose or enter one | `OPENAI_API_KEY` | `SnipDoTranslate/OpenAICompatible` |
| DeepSeek (official API) | `https://api.deepseek.com` | `deepseek-v4-flash` | `DEEPSEEK_API_KEY` | `SnipDoTranslate/DeepSeek` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-5.6-luna` | `OPENROUTER_API_KEY` | `SnipDoTranslate/OpenRouter` |

**OpenAI-compatible** is a separate configuration for the address and model of OpenAI or any compatible service. GPTSAPI keeps its original defaults and credentials, and new installations still select GPTSAPI by default. The model IDs in the table are only the app's presets; which models you can actually use depends on the service and your account.

To configure or switch models:

1. Open Settings, choose a provider and enter that service's API key. With **OpenAI-compatible (custom)** you can edit the base URL; the other providers' URLs are read-only. The base URL is the API root the service gives you, without `/chat/completions` or `/models`.
2. Click **Fetch models**. The app requests `/models` at the address you entered in the background, using the key you just typed if there is one, otherwise the selected provider's environment variable or Windows credential.
3. Once models are fetched, type in the model list to search; matching is case-insensitive on any part of the model ID. You can also pick a model or type a full model ID. If the service has no model list or fetching fails, you can still enter a model by hand and save.
4. Set **Image input** to match the selected model, and only turn it on when the service and model really accept images. When it's off, the app refuses OCR before sending anything and keeps the source file it was given. The DeepSeek preset has image input off.
5. Under **Request options**, tick **Turn off thinking** if needed, then save. For OpenRouter the request includes `reasoning: {"effort": "none"}`; for other providers it includes the `thinking: {"type": "disabled"}` extension, which only works with services and models that support it. The DeepSeek preset has it on. Turning thinking off usually shortens the wait for the first word noticeably; if the selected model requires reasoning and returns an error, untick it.

Each provider keeps its own base URL, model and capability options, and keeps using the saved configuration after switching providers or restarting; changing models doesn't require rebuilding the app. API keys are also stored per provider, and leaving the key field blank never clears an existing credential. If no key is given and none is available, the app asks for one when a request needs it. Fetching models only reads the list and never saves the key you typed.

Common command-line entry points:

```powershell
# Show the window; the same as running without arguments
& .\SnipDoTranslate.exe --show

# Pass text directly
& .\SnipDoTranslate.exe "Hello, world."

# Read a UTF-8 text file
& .\SnipDoTranslate.exe --file "C:\path with spaces\input.txt"

# Run OCR on an image, then translate
& .\SnipDoTranslate.exe --image "C:\path with spaces\image.png"
```

`--file` and `--image` keep the source file by default. Only add `--delete-after` when you really want a temporary file removed:

```powershell
& .\SnipDoTranslate.exe --file "C:\Temp\selection.txt" --delete-after
& .\SnipDoTranslate.exe --image "C:\Temp\capture.png" --delete-after
```

Deletion only happens after a confirmed handover: either this instance actually received and owns the data, or the running main instance returned an explicit `accepted` acknowledgement. The source file is kept when OCR is busy, the key is missing or its prompt was cancelled, the file can't be read, startup fails, IPC times out or the request is rejected. `--delete-after` can't be used on its own.

Automatic startup uses the `--startup` argument to go straight to the system tray, and never asks for an API key at sign-in; double-clicking the EXE or using `--show` still shows the window as usual. The startup entry is stored only for the current user under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, named `SnipDoTranslate`, and isn't written to `settings.json`. If you move the EXE, run it from the new location and save the startup option again; if the entry was disabled in Windows Task Manager, re-enable it there as well. When running from source, the entry uses `pythonw.exe` from the same folder and the script's absolute path, so the files need to stay where they are.

## SnipDo integration

1. Open `snipdo_script_powershell_code\snipdo_translate.txt`.
2. Change only the first `$exePath` in the script so it points to where you keep `SnipDoTranslate.exe`. The path may contain spaces.
3. Copy the whole script into a SnipDo PowerShell action.
4. Select text in any application and trigger the action.

The script works with Windows PowerShell 5.1. It writes the selected text to a UTF-8 temporary file without a BOM and starts the EXE with `--file <temporary file> --delete-after`. The script stores no key and sets no environment variable; if the EXE fails to start, the script doesn't delete the temporary file, so the original input is kept whenever possible.

## User data and privacy

All writable runtime data lives in the current user's folder:

```text
%LOCALAPPDATA%\SnipDoTranslate\
  settings.json
  translation_history.json
  update_state.json
  logs\
    SnipDoTranslate.log
    SnipDoTranslate.log.1 ... .3
```

- `translation_history.json` keeps up to 50 history entries, which may contain source text and translations; treat it as sensitive user data.
- `settings.json` stores whether the tool is on, the translation shortcut, the show-window shortcut, the back-translation and completion-notification switches, the interface language, the selected API provider and each provider's base URL, model and capability options. It never stores API keys. Settings files from older versions get the missing defaults filled in (an older file opens in English) and keep their provider choice.
- `update_state.json` only stores when updates were last checked and the newer version that check found, so the Settings button can show it again soon after a restart; deleting it restores the default checking behavior.
- The formula renderer may create a cache that holds only a font index, never source text or translations. The single-file EXE uses an isolated temporary folder that is cleaned up on exit; running from source may use `%LOCALAPPDATA%\SnipDoTranslate\matplotlib-cache\`.
- The log consists of fixed events as UTF-8 JSON lines and never records source text, translations, OCR content, API keys, full paths or exception messages.
- Each log file holds at most 512 KiB, with 3 rotated backups, for a total of about 2 MiB.
- API keys aren't in this folder; they are stored per user and per provider in Windows Credential Manager.

If an old `translation_history.json` or `.gptsapi_api_key` is found, the app copies it over only when the new target doesn't exist yet. Migration never deletes, overwrites or edits the old files, and never overwrites existing history or credentials with old data.

## Offline self-test

This self-test reads no real key and sends no translation API request:

```powershell
& .\SnipDoTranslate.exe --self-test offline
if ($LASTEXITCODE -ne 0) { throw "SnipDoTranslate offline self-test failed" }
```

It checks the Windows x64 runtime, the bundled resources, offline LaTeX formula rendering, writing to the local data folder and local IPC encoding. It exits with `0` on success and a non-zero code on failure.

## SHA-256 verification

The release also includes `SnipDoTranslate.exe.sha256`. Check it with Windows PowerShell:

```powershell
$expected = ((Get-Content -LiteralPath .\SnipDoTranslate.exe.sha256 -TotalCount 1) -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath .\SnipDoTranslate.exe).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 mismatch; don't run this file" }
"SHA-256 verified: $actual"
```

## Development and building

End users don't need the tools in this section. The build computer needs Windows 10/11 x64, 64-bit Python and the project's build dependencies. The whole automated test run stays offline, uses no real key and never calls a translation API.

Set up `.venv` in the root of this worktree, then run:

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
& .\.venv\Scripts\python.exe -m pip install pytest pyinstaller
PowerShell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

The build script runs these steps strictly in order:

1. Run all offline tests and confirm the build Python is x64.
2. Build the PyInstaller `onedir` preflight output.
3. Check the onedir output's PE x64 format and run `--self-test offline`.
4. Only after the preflight passes, build the final `onefile` EXE.
5. Check the onefile EXE's PE x64 format, unsigned state and offline self-test, and write the SHA-256 file.

The final output is:

```text
dist\SnipDoTranslate.exe
dist\SnipDoTranslate.exe.sha256
```

Local builds and self-tests don't replace a final manual smoke test on a clean Windows 10/11 x64 computer without Python.

Interface text lives in `app_i18n.py`, with one table per language. To add a language, add its code and native name to `UI_LANGUAGES` and a table with the same keys and placeholders as the English one; the tests check that every table matches. Model prompts aren't interface text and stay in `snipdo_translate.pyw`.
