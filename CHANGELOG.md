# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

What changed in each SnipDo Translate release. The app shows this list on the **About** page in Settings.

## 12.6.0 — 2026-11-05

- Update checks no longer interrupt you with a pop-up. When a new version is available, the gear on the **Settings** button turns into a blue download icon; click it to go straight to the update.
- New **About** page in Settings with the installed version, the update status, **Update and restart**, **Release notes**, **Check for updates** and this changelog. When an update is available, its release notes appear at the top of the changelog.
- **Update and restart** saves the changes you made in Settings before it updates.
- The tray menu's **About** and **Check for updates…** open the About page, and a manual check shows its result there.
- If the last check found a newer version, the app checks again right after a restart, so the download icon comes back without waiting 6 hours.
- **Later** and **Skip this version** are gone along with the pop-up.

## 12.5.0 — 2026-11-04

- The interface is in English by default.
- **Settings** → **General** → **Language** switches between English and 简体中文 as soon as you click **Save**, with no restart.
- Language lists name each language in its own language (中文, 日本語, Français…).
- The interface language never changes the translation direction, the target language or what is sent to the model.

## 12.4.1 — 2026-11-04

- Subscripts and superscripts in formulas are larger and never smaller than 10 px, including nested ones.
- Formulas match the size of the surrounding text and sit on its baseline.
- Fractions in display formulas are no longer shrunk, and formulas wider than the window break at `\quad` and `\qquad` instead of being scaled down.
- Display formulas no longer leave large gaps above and below.

## 12.4.0 — 2026-11-04

- Optional back-translation translates the result back into the source language, below the translation, so you can check its meaning. Only the translation is sent. Off by default.
- While a translation runs, **Translate** turns into **Stop**; the text received so far is kept and marked as stopped.
- Long texts no longer slow down the window or the whole computer; rendering a very long translation is about 40 times faster.
- Settings is split into **General**, **Shortcuts**, **Translation** and **API** pages.
- Windows notifications when a translation finishes can be turned off.
- The update notification can open the release page on GitHub.
- Updates are checked every 6 hours instead of once a day.
- Clicking in the source or translation clears the locate highlight.

## 12.3.4 — 2026-11-02

- The main window keeps its size after moving between monitors with different scaling.
- A maximized window stays maximized when reopened from the tray.

## 12.3.3 — 2026-10-29

- **Locate in translation** and **Locate in source** work for History entries and manual translations, not just selection results.
- **Clear** and **Edit** remove leftover formatting from the input box.

## 12.3.2 — 2026-10-27

- Faster responses from OpenRouter when thinking is turned off.
- The connection to the provider stays ready for 15 minutes after a translation, so the next one starts sooner.
- No pause when the first formula appears.
- Results render as Markdown while they stream in, without moving the scroll position.

## 12.3.1 — 2026-10-20

- A new **Edit** button opens the selected text in manual input mode.
- The **Translate** button uses a subdued blue-gray.
- The GPTSAPI, DeepSeek and OpenRouter addresses are read-only; use the OpenAI-compatible provider for a custom address.

## 12.3.0 — 2026-10-16

- **Update and restart** downloads, verifies and installs updates inside the app, and restores the old version if something goes wrong.
- The app can start when you sign in to Windows, in the tray. Off by default.
- Press Esc while recording to remove the show-window shortcut.
- Selection capture with the translation shortcut is more reliable.

## 12.2.0 — 2026-09-23

- OpenAI-compatible providers are configured separately from GPTSAPI.
- Each provider has its own address, API key, model, OCR and thinking options, kept when you switch providers or restart.
- Fetch and search the available models, or enter a model ID by hand.
- A lighter gear icon replaces the shortcut button; hover over it to see the shortcut.

## 12.1.1 — 2026-09-21

- Fixed the app closing unexpectedly when it checked for updates.

## 12.1.0 — 2026-09-20

- New OpenRouter provider for translation, dictionary lookups, alignment and image OCR.
- The version appears in the main window, Settings, the tray's **About** item and the EXE's file properties.
- The app tells you when a new release is published on GitHub.
