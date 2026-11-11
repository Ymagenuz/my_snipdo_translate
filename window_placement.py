"""Where the translation window was, so it reopens there as other apps do.

The window keeps the place and size the user gave it, maximized or not,
also across restarts. Only when no screen shows enough of its top edge
to drag it by, as when the monitor it was on is unplugged, does it move:
to the middle of the screen with the mouse pointer.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

WINDOW_PLACEMENT_FILE_NAME = "window_placement.json"
# How much of the window's top edge must be on a screen to drag it by.
GRIP_WIDTH = 120
GRIP_HEIGHT = 16
# Room left around a window placed on a screen afresh.
SCREEN_MARGIN = 40

Rect = tuple[int, int, int, int]  # x, y, width, height
_FIELDS = ("x", "y", "width", "height", "maximized")


@dataclass(frozen=True)
class WindowPlacement:
    """The window's area when not maximized, and whether it was maximized."""

    x: int
    y: int
    width: int
    height: int
    maximized: bool = False


def load_window_placement(path: Path) -> WindowPlacement | None:
    """The saved placement, or None when there is none or it can't be read."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != set(_FIELDS):
        return None
    numbers = [value[name] for name in _FIELDS[:4]]
    # bool is an int too; neither belongs in the other's place.
    if any(type(number) is not int for number in numbers):
        return None
    if type(value["maximized"]) is not bool:
        return None
    if value["width"] <= 0 or value["height"] <= 0:
        return None
    return WindowPlacement(*numbers, maximized=value["maximized"])


def save_window_placement_atomic(path: Path, placement: WindowPlacement) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(asdict(placement), handle, indent=2)
            handle.write("\n")
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def can_be_grabbed(frame: Rect, screens: Iterable[Rect]) -> bool:
    """Whether one screen shows enough of the window's top edge to drag it by."""
    x, y, width, _height = frame
    for screen_x, screen_y, screen_width, screen_height in screens:
        across = min(x + width, screen_x + screen_width) - max(x, screen_x)
        down = min(y + GRIP_HEIGHT, screen_y + screen_height) - max(y, screen_y)
        if across >= min(GRIP_WIDTH, width) and down >= GRIP_HEIGHT:
            return True
    return False


def fresh_place(screen: Rect, size: tuple[int, int]) -> Rect:
    """The window in the middle of screen, made smaller if it wouldn't fit."""
    screen_x, screen_y, screen_width, screen_height = screen
    width = min(size[0], max(1, screen_width - 2 * SCREEN_MARGIN))
    height = min(size[1], max(1, screen_height - 2 * SCREEN_MARGIN))
    return (
        screen_x + max(SCREEN_MARGIN, (screen_width - width) // 2),
        screen_y + max(SCREEN_MARGIN, (screen_height - height) // 2),
        width,
        height,
    )
