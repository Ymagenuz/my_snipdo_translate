# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

What changed in each SnipDo Translate release. The app shows this list on the **About** page in Settings.

## 13.1.0 — 2026-11-11

- **Even text on scaled screens.** On a screen set to 150% or another scale, text keeps the true shapes of its letters, so the strokes of a Chinese character are no longer one pixel thick in some places and two in others. On a 100% screen text stays crisp, also while a scaled screen is connected, and it switches as you move the window between them.
- **The window stays where you put it.** It reopens where you left it, at the size you gave it and maximized if it was, also after a restart, instead of in the middle of the main screen, and a new translation no longer resizes it. If the monitor it was on is unplugged, it opens in the middle of the screen with the mouse pointer.

## 13.0.0 — 2026-11-06

- **Snip and read text.** Press `Ctrl+Alt+O`, click **OCR** or choose **Snip and read text** in the tray to snip part of the screen with Windows' own snip; the text in it is read and translated. The window steps aside while you snip, shows the snip in the input box while it's read and offers **Stop**. The text arrives marked **From image**, with **Show image** and **Copy text**; fix a misread word and press `Ctrl+Enter` to translate again. Change or remove the shortcut on the **Shortcuts** page in Settings.
- Press `Ctrl+V` with a screenshot in the input box, or drop an image file on the window, to read it the same way. **OCR** no longer reads the clipboard on its own, and a failed or stopped read gives back what you had typed.
- The input box shows Markdown as formatting, as MarkText does: headings, lists, quotes, code, emphasis and links, with their marks shown in grey at the caret so you can edit them. MarkText's shortcuts and the right-click **Paragraph** and **Format** menus work too. Pasted web pages and documents keep their structure, and so does the translation.
- The window takes the icon's colours: blue for the source and back-translation, teal for the translation. The source boxes have a faint tint, **Translate** is tinted blue and **Copy** teal, and translations fade in paragraph by paragraph as they arrive.
- A failed translation keeps what arrived and shows the error in a red note with **Try again**; **Stop** leaves a grey **Stopped** note. **Stop** is a small chip beside the translation's heading, so no button moves.
- Smaller touches: rounded check boxes, one style for menus and tooltips, "Mouse back button" instead of "XButton1", short **History** times such as "Today 04:00", a box sized to a looked-up word, and a hint in the empty result naming the shortcut to press.

## 12.7.0 — 2026-11-05

- Scrolling glides smoothly instead of jumping, one frame per refresh of your monitor (for example 100 or 144 times a second). Locating text glides to it too. With **Animation effects** turned off in Windows, everything moves at once as before.
- In the read-only translation, back-translation and changelog, Page Up/Page Down, Space, Home/End and the arrow keys glide as well.
- The **Locate in translation** / **Locate in original** button and the pages in Settings fade in, and the previous translation dims gently when you click **Edit**.
- Scroll bars are slim rounded lines that rest on the text box's own background and widen when you point at them.
- Dropdowns show a simple chevron that turns over while the list is open, and their lists have rounded rows with the **History** menu's purple highlight.
- Scrolling stays smooth in a large or maximized window: the window's shadow is no longer redrawn on every frame.

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
