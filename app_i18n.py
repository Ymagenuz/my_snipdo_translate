"""User-interface text for every supported language.

Model prompts are not UI text and stay where they are built. To add a
language, add its code and native name to UI_LANGUAGES and a table to
_MESSAGES with the same keys and placeholders as the English table.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


DEFAULT_UI_LANGUAGE = "en"

# Each language is listed by its own name so people can always find theirs.
UI_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("zh-CN", "简体中文"),
)
UI_LANGUAGE_CODES = frozenset(code for code, _name in UI_LANGUAGES)


_ENGLISH = {
    # Settings dialog
    "settings.window_title": "Settings",
    "settings.title": "SnipDo Translate Settings · v{version}",
    "settings.save": "Save",
    "settings.cancel": "Cancel",
    "settings.general": "General",
    "settings.general.hint": (
        "The switch below controls global selection translation; the main "
        "window and SnipDo requests keep working either way."
    ),
    "settings.enabled.row": "Enable tool",
    "settings.enabled.check": "Enable global selection translation",
    "settings.startup.row": "Startup",
    "settings.startup.check": "Start when I sign in to Windows (in the tray)",
    "settings.startup.tooltip": (
        "Applies only to the current Windows user. The main window stays "
        "hidden at automatic startup; uncheck to turn it off."
    ),
    "settings.startup.unavailable": (
        "Can't read the startup setting; you can still change the other options."
    ),
    "settings.notifications.row": "Notifications",
    "settings.notifications.check": "Show a Windows notification when a translation finishes",
    "settings.notifications.note": (
        "Only when the main window isn't in front. When this is off, no "
        "notification appears, but the tray icon still shows an unread badge."
    ),
    "settings.language.row": "Language",
    "settings.language.note": (
        "Menus, buttons and messages switch when you click Save. This doesn't "
        "change the translation direction or target language."
    ),
    "settings.shortcuts": "Shortcuts",
    "settings.shortcuts.hint": (
        "While recording, press Esc to remove the show-window shortcut. The "
        "translation shortcut can't be removed; Esc only cancels the change. "
        "Mouse shortcuts block the button's usual Back, Forward or middle-click action."
    ),
    "settings.shortcut.translate.row": "Translation shortcut",
    "settings.shortcut.show_window.row": "Show-window shortcut",
    "settings.translation": "Translation",
    "settings.translation.hint": (
        "Back-translation translates the result back into the source language "
        "so you can check that it carries the original meaning."
    ),
    "settings.back_translation.row": "Back-translation",
    "settings.back_translation.check": "Back-translate automatically after each translation",
    "settings.back_translation.note": (
        "Each translation sends one extra request; dictionary results aren't "
        "back-translated. You can also turn this on or off with the "
        "“Back-translate” button next to the translation heading in the main window."
    ),
    "settings.api": "API",
    "settings.api.hint": (
        "Every provider uses the OpenAI Chat Completions format. API keys are "
        "stored per provider in Windows Credential Manager."
    ),
    "settings.api.provider.row": "Provider",
    "settings.api.key.row": "API key",
    "settings.api.key.placeholder": "Enter a new API key (leave blank to keep the current one)",
    # Kept short so the longest provider name fits in the field.
    "settings.api.key.placeholder_for": "Enter a new {provider} API key",
    "settings.api.base_url.row": "Base URL",
    "settings.api.base_url.custom_tooltip": (
        "Enter the API root URL, including any prefix the service needs such "
        "as /v1. Don't add /chat/completions."
    ),
    "settings.api.base_url.fixed_tooltip": (
        "This provider's base URL is read-only. To use your own URL, choose {provider}."
    ),
    "settings.api.model.row": "Model",
    "settings.api.model.placeholder": "Search, select or enter a model ID",
    "settings.api.model.fetch": "Fetch models",
    "settings.api.vision.row": "Image input",
    "settings.api.vision.check": "The selected model accepts images (OCR)",
    "settings.api.thinking.row": "Request options",
    "settings.api.thinking.check": "Turn off thinking (only for services that support it)",
    "settings.api.model.status": (
        "Fetch the model list from the provider, or enter a model ID directly."
    ),
    "settings.api.model.invalid_url": (
        "Enter a valid HTTP(S) base URL. It can't contain a key, query or fragment."
    ),
    "settings.api.model.needs_key": (
        "Enter this provider's API key before fetching models, or enter a model ID directly."
    ),
    "settings.api.model.fetching": "Fetching the model list…",
    "settings.api.model.fetched": (
        "Found {count} models. Type to search and pick one; check whether the "
        "model you pick accepts images."
    ),
    "settings.api.model.fetch_failed": (
        "Couldn't fetch the model list. Check the base URL, API key and "
        "network, or enter a model ID directly."
    ),
    "settings.api.status.switching": (
        "Switching to {provider}. If you leave the key blank, the provider's "
        "saved key or {variable} is used; if neither exists, you'll be asked "
        "for a key on the next request."
    ),
    "settings.api.status.environment": (
        "The current API key comes from the {variable} environment variable; "
        "it takes priority over the saved key at the next start."
    ),
    "settings.api.status.configured": (
        "An API key is set; leave the field blank to keep it. To protect it, "
        "the saved key isn't shown."
    ),
    "settings.api.status.missing": (
        "No API key is set yet; a new key is stored securely in Windows Credential Manager."
    ),
    "settings.api.status.no_vision": " Image input is off, so image OCR isn't available.",
    "settings.api.invalid_config": (
        "{provider}: enter a valid HTTP(S) base URL and model ID. The base URL "
        "can't contain a key, query or fragment."
    ),
    # Shortcut recorder
    "shortcut.not_set": "Not set",
    "shortcut.inactive": "Inactive",
    "shortcut.tooltip.removable": (
        "Click, then press a new global shortcut. Press Esc to remove this shortcut."
    ),
    "shortcut.tooltip.required": (
        "The translation shortcut can't be removed. Click to record a new one; "
        "Esc cancels the change."
    ),
    "shortcut.capture.removable": "Press a shortcut, or Esc to remove…",
    "shortcut.capture.required": "Press a shortcut, or Esc to cancel…",
    "shortcut.error.unknown_key": "That key can't be used. Try another combination.",
    "shortcut.error.needs_modifier": (
        "Ordinary keys need Ctrl, Alt, Shift or Win; F1–F24 can be used alone."
    ),
    "shortcut.error.mouse_button": (
        "Mouse shortcuts can use XButton1, XButton2 or the middle button. {hint}"
    ),
    "shortcut.hint.remove": "Esc removes this shortcut.",
    "shortcut.hint.cancel": "Esc cancels the change.",
    # Tray menu, tooltips and notifications
    "tray.show_window": "Show main window",
    "tray.show_window_with_shortcut": "Show main window ({shortcut})",
    "tray.disable": "Disable",
    "tray.settings": "Settings…",
    "tray.ocr_clipboard": "OCR clipboard image",
    "tray.check_updates": "Check for updates…",
    "tray.about": "About SnipDo Translate ({version})",
    "tray.quit": "Quit",
    "tray.unread": " · Unread translation",
    "tray.disabled": "{app} · Global selection translation is off",
    "tray.shortcut_inactive": "{app} · Shortcut inactive",
    "tray.show_window_status": " · Show window {status}",
    "about.title": "About {app}",
    "about.text": (
        "{app}\nVersion {version}\n\nChecks for new versions automatically. "
        "You can also choose “Check for updates” from the tray menu."
    ),
    "notify.translation_finished": "Translation finished",
    "notify.shortcut_failed": (
        "Couldn't register the shortcut. Choose another one in Settings."
    ),
    "notify.show_window_shortcut_failed": (
        "Couldn't register the show-window shortcut. Choose another one in Settings."
    ),
    "notify.release_shortcut": "Release the shortcut, then try selection translation again.",
    "notify.no_selection": (
        "No selected text found. Make sure the text can be copied from that "
        "window, release the shortcut and try again."
    ),
    # Warnings shown while saving settings
    "warning.startup.title": "Couldn't change startup",
    "warning.startup.text": (
        "Your other settings were saved, but the startup setting couldn't be "
        "changed. Try again later."
    ),
    "warning.shortcut_conflict.title": "Shortcut conflict",
    "warning.shortcut_conflict.text": (
        "The translation shortcut and the show-window shortcut can't be the same."
    ),
    "warning.invalid_key.title": "Invalid API key",
    "warning.invalid_key.text": "Enter a valid API key. Your other settings weren't changed.",
    "warning.provider_busy.title": "Provider not switched",
    "warning.provider_busy.text": (
        "A request is still running. Save the provider settings again in a moment."
    ),
    "warning.shortcut_unavailable.title": "Shortcut unavailable",
    "warning.show_window_shortcut_unavailable.title": "Show-window shortcut unavailable",
    "warning.shortcut_unavailable.text": (
        "Another program may be using this shortcut. The previous shortcut stays active."
    ),
    "warning.save_failed.title": "Couldn't save settings",
    "warning.save_failed.text": (
        "Settings couldn't be saved. The previous on/off state and shortcuts were restored."
    ),
    "warning.back_translation_save_failed.text": (
        "Couldn't save the back-translation setting. Try again later."
    ),
    "warning.key_not_stored.title": "Couldn't save API key",
    "warning.key_not_stored.text": (
        "Your provider, on/off and shortcut settings were saved, but the API "
        "key couldn't be written to Windows Credential Manager."
    ),
    "warning.no_model.title": "No model selected",
    "warning.no_model.text": "Fetch and select a model in Settings, or enter a model ID.",
    "prompt.api_key.title": "Enter {provider} API key",
    "prompt.api_key.text": (
        "No {provider} API key was found. Enter one (it will be saved to "
        "Windows Credential Manager):"
    ),
    # Main window
    "main.settings": "Settings",
    "main.settings_tooltip": "Settings · Translation shortcut: {shortcut}",
    "main.model_tooltip": "Provider: {provider}\nBase URL: {url}\nModel: {model}",
    "main.mode.auto": "Mode: Auto",
    "main.mode.dictionary": "Mode: Dictionary",
    # No caption: the two language lists explain themselves, and the longer
    # English headings need the room. A language with a caption shows it.
    "main.language": "",
    "main.language.tooltip": "Source language and translation/definition language",
    "main.language.source_tooltip": "Source language",
    "main.language.target_tooltip": "Translation/definition language",
    "main.label.original_auto": "ORIGINAL (AUTO DETECT)",
    "main.label.original": "ORIGINAL",
    "main.label.ocr_image": "OCR IMAGE",
    "main.label.auto_translation": "AUTO TRANSLATION",
    "main.label.chinese_translation": "CHINESE TRANSLATION",
    "main.label.english_translation": "ENGLISH TRANSLATION",
    "main.label.language_translation": "{language} TRANSLATION",
    "main.label.dictionary": "DICTIONARY",
    "main.label.ocr": "OCR",
    "main.label.back_translation": "BACK TRANSLATION",
    "main.label.back_translation_into": "BACK TRANSLATION · {language}",
    "main.back_translation": "Back-translate",
    "main.back_translation.tooltip": (
        "When on, each finished translation is translated back into the source "
        "language so you can check the meaning. Each translation sends one "
        "extra request; dictionary results aren't back-translated."
    ),
    "main.stop_back_translation": "Stop",
    "main.stop_back_translation.tooltip": "Stop back-translation and keep what has arrived",
    "main.edit": "Edit",
    "main.edit.tooltip": "Edit the source text and translate again",
    "main.clear": "Clear",
    "main.ocr": "OCR",
    "main.ocr.tooltip": "Read the text in the clipboard image and translate it",
    "main.ocr.busy": "OCR...",
    "main.history": "History",
    "main.history.empty": "No history",
    "main.history.clear": "Clear history",
    "main.translate": "Translate (Ctrl+Enter)",
    "main.translating": "Translating...",
    "main.looking_up": "Looking up...",
    "main.stop": "Stop",
    "main.stop.tooltip": "Stop translating and keep what has arrived",
    "main.copy": "Copy",
    "main.copied": "Copied",
    "main.hide": "Hide",
    "main.locate.translation": "Locate in translation",
    "main.locate.original": "Locate in original",
    "main.locate.busy": "Locating...",
    "main.placeholder.dictionary": (
        "Type or paste a word, phrase or term to look up...\n"
        "Press Ctrl + Enter to look it up"
    ),
    "main.placeholder.translate": (
        "Type or paste the text to translate...\nPress Ctrl + Enter to translate"
    ),
    # Messages in the result and back-translation panes
    "message.ocr_running": "Reading text from the image...",
    "message.ocr_error": "[OCR error: {error}]",
    "message.ocr_unsupported": (
        "[The current {provider} provider doesn't support image OCR; switch to "
        "one that does in Settings]"
    ),
    "message.ocr_no_key": "[No API key is set; OCR cancelled]",
    "message.translation_no_key": "[No API key is set; translation cancelled]",
    "message.translation_error": "[Translation error: {error}]",
    "message.stopped": "[Stopped]",
    "message.ipc_no_key": (
        "[No API key is set. Start a translation in this window first and enter a key.]"
    ),
    "message.ipc_ocr_busy": "[OCR is busy. Try again later.]",
    "message.back_translation_same_language": (
        "The source and the translation are in the same language; there's "
        "nothing to back-translate."
    ),
    "message.back_translation_no_key": "[No API key is set; can't back-translate]",
    "message.back_translation_error": "[Back-translation error: {error}]",
    "message.back_translation_running": "Back-translating…",
    "message.back_translation_stopped": "[Back-translation stopped]",
    "message.back_translation_empty": "[Back-translation came back empty]",
    # Error details placed into the messages above
    "error.cannot_start": "couldn't start",
    "error.cannot_read_image": "couldn't read the image",
    "error.no_text_found": "no text found",
    "error.no_api_key": "No {provider} API key is set",
    "error.ocr_unsupported": "{provider} doesn't support image OCR",
    "error.clipboard_no_image": "the clipboard has no image",
    "error.clipboard_unreadable": "couldn't read the clipboard image",
    "error.clipboard_not_png": "couldn't convert the clipboard image to PNG",
    # Dictionary and target languages in the main window
    "language.auto": "Auto",
    "language.default": "Default",
    "language.zh": "Chinese",
    "language.en": "English",
    "language.ja": "Japanese",
    "language.ko": "Korean",
    "language.fr": "French",
    "language.de": "German",
    "language.es": "Spanish",
    "language.ru": "Russian",
    "language.it": "Italian",
    # API providers
    "provider.gptsapi.name": "GPTSAPI",
    "provider.gptsapi.short": "GPTSAPI",
    "provider.deepseek.name": "DeepSeek (official API)",
    "provider.deepseek.short": "DeepSeek",
    "provider.openrouter.name": "OpenRouter",
    "provider.openrouter.short": "OpenRouter",
    "provider.openai_compatible.name": "OpenAI-compatible (custom)",
    "provider.openai_compatible.short": "OpenAI-compatible",
    # Updates
    "update.status.title": "Check for updates",
    "update.status.current": "You're on version {version}; no update is available.",
    "update.check_failed": "Can't check for updates right now. Check your network and try again.",
    "update.available.title": "A new version of SnipDo Translate is available",
    "update.available.text": "Version {version} is available\nCurrent version: {current}",
    "update.available.details": (
        "Download and verify the update in the app; it then installs and "
        "restarts automatically.\nYou can keep working or cancel during the "
        "download; the restart waits for translation, OCR and Settings to "
        "finish. Settings, history and API keys are kept.\nYou can also open "
        "the GitHub page to read the release notes and download it yourself."
    ),
    "update.button.install": "Update and restart",
    "update.button.github": "Open GitHub page",
    "update.button.later": "Later",
    "update.button.skip": "Skip this version",
    "update.browser_failed": "Couldn't open the browser. Visit:\n{url}",
    "update.progress.title": "Updating to {version}",
    "update.progress.cancel": "Cancel",
    "update.progress.checksums": "Downloading update checksums…",
    "update.progress.download": (
        "Downloading update: {received:.1f} / {total:.1f} MB\n"
        "The app restarts automatically once the download is verified. "
        "Settings, history and API keys are kept."
    ),
    "update.progress.verified": "Download verified; preparing the update…",
    "update.progress.restarting": "Preparing to restart…",
    "update.progress.waiting": (
        "The update is ready and will restart once translation, OCR or Settings finish…"
    ),
    "update.progress.installing": "Installing the update and restarting…",
    "update.error.download": (
        "The update download failed or didn't pass verification. Check your "
        "network and try again."
    ),
    "update.error.start_download": "Couldn't start the update download. Try again later.",
    "update.error.save_download": (
        "Couldn't save the update file. Check disk space and folder permissions, "
        "then try again."
    ),
    "update.error.prepare": (
        "Couldn't prepare the update. Make sure the app's folder is writable and "
        "has enough free space, then try again."
    ),
    "update.error.launch": (
        "Couldn't start installing the update; the current version was kept. "
        "Try again later."
    ),
    "update.error.source_run": (
        "Installing updates directly needs the Windows single-file release; "
        "running from Python source can't replace itself."
    ),
    "update.error.folder_build": (
        "Installing updates directly only works with the single-file release; "
        "a folder release can't replace just the executable."
    ),
    "update.error.not_regular_exe": (
        "The app must be a regular Windows .exe file in a fixed location."
    ),
    "update.error.folder_not_writable": (
        "The app's folder isn't writable. Move the app to a folder you can "
        "write to and try again."
    ),
    "update.failed.title": "SnipDo Translate update failed",
    "update.failed.paths": "\n\nApp: {target}\nBackup: {backup}",
}

_SIMPLIFIED_CHINESE = {
    "settings.window_title": "设置",
    "settings.title": "翻译工具设置 · v{version}",
    "settings.save": "保存",
    "settings.cancel": "取消",
    "settings.general": "常规",
    "settings.general.hint": "启用状态控制全局划词翻译；主窗口和 SnipDo 调用仍可继续使用。",
    "settings.enabled.row": "启用工具",
    "settings.enabled.check": "启用全局划词翻译",
    "settings.startup.row": "开机启动",
    "settings.startup.check": "登录 Windows 时自动启动（后台托盘）",
    "settings.startup.tooltip": "仅对当前 Windows 用户生效。自动启动时不弹出主窗口；取消勾选可关闭。",
    "settings.startup.unavailable": "无法读取开机启动设置，可继续修改其他选项。",
    "settings.notifications.row": "通知",
    "settings.notifications.check": "翻译完成时显示 Windows 通知",
    "settings.notifications.note": (
        "仅在主窗口不在前台时通知。关闭后不再弹出通知，托盘图标仍会显示未读标志。"
    ),
    "settings.language.row": "界面语言",
    "settings.language.note": "点击“保存”后切换菜单、按钮和提示的语言；不影响翻译方向和目标语言。",
    "settings.shortcuts": "快捷键",
    "settings.shortcuts.hint": (
        "录入时按 Esc 可移除显示窗口快捷键；翻译快捷键不可移除，Esc 仅取消修改。"
        "鼠标快捷键会拦截对应的后退、前进或中键动作。"
    ),
    "settings.shortcut.translate.row": "翻译快捷键",
    "settings.shortcut.show_window.row": "显示窗口快捷键",
    "settings.translation": "翻译",
    "settings.translation.hint": "回译会把译文再翻译回原文语言，便于核对译文是否准确传达了原意。",
    "settings.back_translation.row": "回译",
    "settings.back_translation.check": "翻译完成后自动回译",
    "settings.back_translation.note": (
        "每次翻译会额外发送一次请求；词典结果不回译。"
        "也可点击主窗口译文标题旁的“回译”按钮快速开关。"
    ),
    "settings.api": "API 接口",
    "settings.api.hint": (
        "所有接口均使用 OpenAI Chat Completions 兼容格式；"
        "API Key 按接口分别保存在 Windows 凭据管理器中。"
    ),
    "settings.api.provider.row": "API 接口",
    "settings.api.key.row": "API Key",
    "settings.api.key.placeholder": "输入新 API Key（留空不修改）",
    "settings.api.key.placeholder_for": "输入新的 {provider} API Key（留空不修改）",
    "settings.api.base_url.row": "服务地址",
    "settings.api.base_url.custom_tooltip": (
        "填写 API 根地址，包含服务要求的 /v1 等前缀，无需填写 /chat/completions。"
    ),
    "settings.api.base_url.fixed_tooltip": "此接口的服务地址为只读；如需自定义地址，请选择 {provider}。",
    "settings.api.model.row": "模型",
    "settings.api.model.placeholder": "搜索、选择或输入模型 ID",
    "settings.api.model.fetch": "获取模型",
    "settings.api.vision.row": "图片能力",
    "settings.api.vision.check": "所选模型支持图片输入（OCR）",
    "settings.api.thinking.row": "请求选项",
    "settings.api.thinking.check": "发送禁用思考参数（仅限支持此参数的服务）",
    "settings.api.model.status": "可从接口获取模型列表，也可直接输入模型 ID。",
    "settings.api.model.invalid_url": "请填写有效的 HTTP(S) 服务地址；地址不能包含密钥、查询参数或片段。",
    "settings.api.model.needs_key": "请先输入此接口的 API Key，再获取模型；也可直接输入模型 ID。",
    "settings.api.model.fetching": "正在获取模型列表…",
    "settings.api.model.fetched": "已获取 {count} 个模型。输入关键词搜索并选择；图片能力需根据所选模型确认。",
    "settings.api.model.fetch_failed": "无法获取模型列表。请检查服务地址、API Key 和网络，或直接输入模型 ID。",
    "settings.api.status.switching": (
        "将切换到 {provider}。留空时会使用该接口已有的凭据或"
        " {variable}；若均不存在，下次请求时会提示输入。"
    ),
    "settings.api.status.environment": (
        "当前 API Key 来自 {variable} 环境变量；它会在下次启动时优先于已保存的 Key。"
    ),
    "settings.api.status.configured": "已设置 API Key。为保护密钥，输入框不会回填原值。",
    "settings.api.status.missing": "尚未设置 API Key；新 Key 将安全保存到 Windows 凭据管理器。",
    "settings.api.status.no_vision": " 当前未启用图片能力，不支持图片 OCR。",
    "settings.api.invalid_config": (
        "{provider}：请填写有效的 HTTP(S) 服务地址和模型 ID。服务地址不能包含密钥、查询参数或片段。"
    ),
    "shortcut.not_set": "未设置",
    "shortcut.inactive": "未激活",
    "shortcut.tooltip.removable": "点击后按下新的全局快捷键；按 Esc 移除此快捷键",
    "shortcut.tooltip.required": "翻译快捷键不可移除；点击后录入新快捷键，Esc 取消修改",
    "shortcut.capture.removable": "请按下快捷键，Esc 移除…",
    "shortcut.capture.required": "请按下快捷键，Esc 取消…",
    "shortcut.error.unknown_key": "无法识别该按键，请换一个组合键。",
    "shortcut.error.needs_modifier": "普通按键需要搭配 Ctrl、Alt、Shift 或 Win；F1–F24 可单独使用。",
    "shortcut.error.mouse_button": "鼠标快捷键支持 XButton1、XButton2 和中键；{hint}",
    "shortcut.hint.remove": "Esc 移除此快捷键。",
    "shortcut.hint.cancel": "Esc 取消修改。",
    "tray.show_window": "显示主窗口",
    "tray.show_window_with_shortcut": "显示主窗口（{shortcut}）",
    "tray.disable": "禁用",
    "tray.settings": "设置…",
    "tray.ocr_clipboard": "OCR 剪贴板图片",
    "tray.check_updates": "检查更新…",
    "tray.about": "关于 SnipDo Translate（{version}）",
    "tray.quit": "彻底退出",
    "tray.unread": " · 有未读译文",
    "tray.disabled": "{app} · 全局划词翻译已禁用",
    "tray.shortcut_inactive": "{app} · 快捷键未激活",
    "tray.show_window_status": " · 显示窗口 {status}",
    "about.title": "关于 {app}",
    "about.text": "{app}\n版本 {version}\n\n自动检查新版本，也可从托盘菜单选择“检查更新”。",
    "notify.translation_finished": "翻译完成",
    "notify.shortcut_failed": "快捷键注册失败；请在设置中选择其他快捷键。",
    "notify.show_window_shortcut_failed": "显示窗口快捷键注册失败；请在设置中选择其他快捷键。",
    "notify.release_shortcut": "请松开快捷键后重试划词翻译。",
    "notify.no_selection": "未检测到选中文字，请确认原窗口可复制文本，松开快捷键后重试。",
    "warning.startup.title": "开机启动设置失败",
    "warning.startup.text": "其他设置已保存，但无法更改开机启动设置。请稍后重试。",
    "warning.shortcut_conflict.title": "快捷键冲突",
    "warning.shortcut_conflict.text": "翻译快捷键和显示窗口快捷键不能相同。",
    "warning.invalid_key.title": "API Key 无效",
    "warning.invalid_key.text": "请输入有效的 API Key；其他设置尚未更改。",
    "warning.provider_busy.title": "接口暂未切换",
    "warning.provider_busy.text": "当前请求尚未结束。请稍后再次保存接口设置。",
    "warning.shortcut_unavailable.title": "快捷键不可用",
    "warning.show_window_shortcut_unavailable.title": "显示窗口快捷键不可用",
    "warning.shortcut_unavailable.text": "该快捷键可能已被其他程序占用。原快捷键仍保持激活。",
    "warning.save_failed.title": "设置保存失败",
    "warning.save_failed.text": "无法保存设置，已恢复原来的启用状态和快捷键。",
    "warning.back_translation_save_failed.text": "无法保存回译设置，请稍后重试。",
    "warning.key_not_stored.title": "API Key 保存失败",
    "warning.key_not_stored.text": (
        "接口设置、启用状态和快捷键已保存，但 API Key 无法写入 Windows 凭据管理器。"
    ),
    "warning.no_model.title": "未选择模型",
    "warning.no_model.text": "请在设置中获取并选择模型，或输入模型 ID。",
    "prompt.api_key.title": "输入 {provider} API Key",
    "prompt.api_key.text": "未检测到 {provider} API Key，请手动输入（将保存到 Windows 凭据管理器）：",
    "main.settings": "设置",
    "main.settings_tooltip": "设置 · 翻译快捷键：{shortcut}",
    "main.model_tooltip": "当前接口：{provider}\n服务地址：{url}\n模型：{model}",
    "main.mode.auto": "模式: Auto",
    "main.mode.dictionary": "模式: 词典",
    "main.language": "语言",
    "main.language.tooltip": "原文语言与译文/释义语言",
    "main.language.source_tooltip": "原文语言",
    "main.language.target_tooltip": "译文/释义语言",
    # These headings and the buttons below have always been English.
    "main.label.original_auto": "ORIGINAL (AUTO DETECT)",
    "main.label.original": "ORIGINAL",
    "main.label.ocr_image": "OCR IMAGE",
    "main.label.auto_translation": "AUTO TRANSLATION",
    "main.label.chinese_translation": "CHINESE TRANSLATION",
    "main.label.english_translation": "ENGLISH TRANSLATION",
    "main.label.language_translation": "{language} TRANSLATION",
    "main.label.dictionary": "DICTIONARY",
    "main.label.ocr": "OCR",
    "main.label.back_translation": "BACK TRANSLATION",
    "main.label.back_translation_into": "BACK TRANSLATION · {language}",
    "main.back_translation": "回译",
    "main.back_translation.tooltip": (
        "开启后，译文完成时自动翻译回原文语言，用于核对含义；"
        "每次翻译额外发送一次请求，词典结果不回译"
    ),
    "main.stop_back_translation": "停止",
    "main.stop_back_translation.tooltip": "停止回译，保留已收到的内容",
    "main.edit": "Edit",
    "main.edit.tooltip": "编辑原文后重新翻译",
    "main.clear": "Clear",
    "main.ocr": "OCR",
    "main.ocr.tooltip": "识别剪贴板图片文字，并自动翻译",
    "main.ocr.busy": "OCR...",
    "main.history": "History",
    "main.history.empty": "No history",
    "main.history.clear": "Clear history",
    "main.translate": "Translate (Ctrl+Enter)",
    "main.translating": "Translating...",
    "main.looking_up": "Looking up...",
    "main.stop": "Stop",
    "main.stop.tooltip": "停止翻译，保留已收到的译文",
    "main.copy": "Copy",
    "main.copied": "Copied",
    "main.hide": "Hide",
    "main.locate.translation": "定位译文",
    "main.locate.original": "定位原文",
    "main.locate.busy": "定位中...",
    "main.placeholder.dictionary": "在此输入或粘贴需要查词的单词、短语或术语...\n按 Ctrl + Enter 开始查词",
    "main.placeholder.translate": "在此输入或粘贴需要翻译的文本...\n按 Ctrl + Enter 开始翻译",
    "message.ocr_running": "正在识别图片文字...",
    "message.ocr_error": "[OCR 出错: {error}]",
    "message.ocr_unsupported": "[当前 {provider} 接口不支持图片 OCR；请在设置中切换到支持图片的接口]",
    "message.ocr_no_key": "[未设置 API Key，已取消 OCR]",
    "message.translation_no_key": "[未设置 API Key，已取消翻译]",
    "message.translation_error": "[翻译出错: {error}]",
    "message.stopped": "[已停止]",
    "message.ipc_no_key": "[未设置 API Key，请先在窗口中发起一次翻译并输入 Key]",
    "message.ipc_ocr_busy": "[OCR 正忙，请稍后重试]",
    "message.back_translation_same_language": "原文与译文为同一语言，无需回译。",
    "message.back_translation_no_key": "[未设置 API Key，无法回译]",
    "message.back_translation_error": "[回译出错: {error}]",
    "message.back_translation_running": "正在回译…",
    "message.back_translation_stopped": "[已停止回译]",
    "message.back_translation_empty": "[回译结果为空]",
    "error.cannot_start": "无法启动",
    "error.cannot_read_image": "无法读取图片",
    "error.no_text_found": "未识别到文字",
    "error.no_api_key": "未设置 {provider} API Key",
    "error.ocr_unsupported": "{provider} 不支持图片 OCR",
    "error.clipboard_no_image": "剪贴板中没有图片",
    "error.clipboard_unreadable": "无法读取剪贴板图片",
    "error.clipboard_not_png": "无法把剪贴板图片转换为 PNG",
    "language.auto": "Auto",
    "language.default": "默认",
    "language.zh": "中文",
    "language.en": "English",
    "language.ja": "日本語",
    "language.ko": "한국어",
    "language.fr": "Français",
    "language.de": "Deutsch",
    "language.es": "Español",
    "language.ru": "Русский",
    "language.it": "Italiano",
    "provider.gptsapi.name": "GPTSAPI",
    "provider.gptsapi.short": "GPTSAPI",
    "provider.deepseek.name": "DeepSeek 官方接口",
    "provider.deepseek.short": "DeepSeek",
    "provider.openrouter.name": "OpenRouter",
    "provider.openrouter.short": "OpenRouter",
    "provider.openai_compatible.name": "OpenAI 兼容接口（自定义）",
    "provider.openai_compatible.short": "OpenAI 兼容",
    "update.status.title": "检查更新",
    "update.status.current": "当前版本为 {version}，未发现可用的更新。",
    "update.check_failed": "暂时无法检查更新，请检查网络后重试。",
    "update.available.title": "SnipDo Translate 有新版本",
    "update.available.text": "发现新版本 {version}\n当前版本：{current}",
    "update.available.details": (
        "在应用内下载并校验更新，然后自动安装并重启。\n"
        "下载期间可继续使用或取消；重启会等待翻译、OCR 和设置完成。"
        "设置、历史记录和 API Key 会保留。\n"
        "也可打开 GitHub 页面查看发行说明并手动下载。"
    ),
    "update.button.install": "更新并重启",
    "update.button.github": "打开 GitHub 页面",
    "update.button.later": "稍后",
    "update.button.skip": "跳过此版本",
    "update.browser_failed": "无法打开浏览器，请访问：\n{url}",
    "update.progress.title": "更新至 {version}",
    "update.progress.cancel": "取消",
    "update.progress.checksums": "正在下载更新校验信息…",
    "update.progress.download": (
        "正在下载更新：{received:.1f} / {total:.1f} MB\n"
        "校验完成后将自动重启。设置、历史记录和 API Key 会保留。"
    ),
    "update.progress.verified": "下载校验完成，正在准备更新…",
    "update.progress.restarting": "正在准备重启…",
    "update.progress.waiting": "更新已就绪，等待翻译、OCR 或设置完成后重启…",
    "update.progress.installing": "正在安装更新并重启…",
    "update.error.download": "更新下载失败或校验未通过，请检查网络后重试。",
    "update.error.start_download": "无法开始下载更新，请稍后重试。",
    "update.error.save_download": "无法保存更新文件。请检查磁盘空间与目录权限后重试。",
    "update.error.prepare": "无法准备更新。请确认程序目录可写、磁盘空间充足，然后重试。",
    "update.error.launch": "无法启动更新安装，当前版本已保留。请稍后重试。",
    "update.error.source_run": (
        "直接安装更新需要运行 Windows 单文件发行版；Python 源码运行方式不支持自动替换。"
    ),
    "update.error.folder_build": "直接安装更新仅支持单文件发行版；文件夹发行版不能只替换可执行文件。",
    "update.error.not_regular_exe": "当前程序必须是位于固定位置的普通 Windows .exe 文件。",
    "update.error.folder_not_writable": "当前程序所在文件夹不可写。请将程序移到有写入权限的文件夹后重试。",
    "update.failed.title": "SnipDo Translate 更新失败",
    "update.failed.paths": "\n\n程序路径：{target}\n备份位置：{backup}",
}

_MESSAGES: Mapping[str, Mapping[str, str]] = MappingProxyType({
    "en": MappingProxyType(_ENGLISH),
    "zh-CN": MappingProxyType(_SIMPLIFIED_CHINESE),
})

_current_language = DEFAULT_UI_LANGUAGE


def ui_language() -> str:
    return _current_language


def set_ui_language(language: str) -> None:
    global _current_language

    if language not in UI_LANGUAGE_CODES:
        raise ValueError("unsupported UI language")
    _current_language = language


def tr(key: str, **values: object) -> str:
    """Return the current language's text for key, falling back to English."""
    text = _MESSAGES[_current_language].get(key)
    if text is None:
        text = _MESSAGES[DEFAULT_UI_LANGUAGE][key]
    return text.format(**values) if values else text


def language_name(code: str) -> str:
    """Name of a dictionary/translation language in the current UI language."""
    return tr(f"language.{code}")


# Language lists name each language in its own language, whatever the UI
# language, so people can always find theirs.
NATIVE_LANGUAGE_NAMES: Mapping[str, str] = MappingProxyType({
    "zh": "中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "fr": "Français",
    "de": "Deutsch",
    "es": "Español",
    "ru": "Русский",
    "it": "Italiano",
})


def native_language_name(code: str) -> str:
    """A language's own name; "auto" and "default" follow the UI language."""
    return NATIVE_LANGUAGE_NAMES.get(code) or language_name(code)


def provider_name(provider_id: str, *, short: bool = False) -> str:
    return tr(f"provider.{provider_id}.{'short' if short else 'name'}")


def messages(language: str) -> Mapping[str, str]:
    return _MESSAGES[language]


__all__ = [
    "DEFAULT_UI_LANGUAGE",
    "NATIVE_LANGUAGE_NAMES",
    "UI_LANGUAGES",
    "UI_LANGUAGE_CODES",
    "language_name",
    "messages",
    "native_language_name",
    "provider_name",
    "set_ui_language",
    "tr",
    "ui_language",
]
