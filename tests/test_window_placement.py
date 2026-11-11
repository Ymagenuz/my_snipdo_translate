import json
from pathlib import Path

import pytest

from window_placement import (
    GRIP_HEIGHT,
    SCREEN_MARGIN,
    WindowPlacement,
    can_be_grabbed,
    fresh_place,
    load_window_placement,
    save_window_placement_atomic,
)

MI = (0, 0, 2560, 1392)
CFORCE = (2560, 0, 1707, 912)


def test_placement_survives_a_save_and_load(tmp_path: Path):
    path = tmp_path / "data" / "window_placement.json"
    placement = WindowPlacement(2700, 80, 640, 760, maximized=True)

    save_window_placement_atomic(path, placement)

    assert load_window_placement(path) == placement
    assert list(tmp_path.glob("data/*.tmp")) == []


@pytest.mark.parametrize(
    "content",
    [
        None,
        "not json",
        "[]",
        json.dumps({"x": 1, "y": 2, "width": 640, "height": 760}),
        json.dumps({"x": 1, "y": 2, "width": 640, "height": 760, "maximized": 1}),
        json.dumps({"x": True, "y": 2, "width": 640, "height": 760, "maximized": False}),
        json.dumps({"x": 1.5, "y": 2, "width": 640, "height": 760, "maximized": False}),
        json.dumps({"x": 1, "y": 2, "width": 0, "height": 760, "maximized": False}),
        json.dumps(
            {"x": 1, "y": 2, "width": 640, "height": 760, "maximized": False, "extra": 0}
        ),
    ],
    ids=[
        "missing",
        "not-json",
        "not-an-object",
        "field-missing",
        "maximized-not-bool",
        "x-is-bool",
        "x-not-whole",
        "no-width",
        "extra-field",
    ],
)
def test_unreadable_placement_is_ignored(tmp_path: Path, content):
    path = tmp_path / "window_placement.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")

    assert load_window_placement(path) is None


def test_window_on_either_screen_can_be_grabbed():
    assert can_be_grabbed((1000, 346, 560, 700), [MI, CFORCE])
    assert can_be_grabbed((2700, 80, 640, 760), [MI, CFORCE])
    # Hanging off the right edge with enough of its top still showing.
    assert can_be_grabbed((CFORCE[0] + CFORCE[2] - 130, 80, 640, 760), [MI, CFORCE])


def test_window_on_an_unplugged_screen_cannot_be_grabbed():
    assert not can_be_grabbed((2700, 80, 640, 760), [MI])


def test_window_with_too_little_of_its_top_showing_cannot_be_grabbed():
    # Its top edge above the top of the screen.
    assert not can_be_grabbed((1000, -GRIP_HEIGHT + 4, 560, 700), [MI])
    # Only a sliver of it on the screen.
    assert not can_be_grabbed((2560 - 60, 300, 560, 700), [MI])


def test_fresh_place_centres_the_window_on_the_screen():
    assert fresh_place(MI, (560, 700)) == (1000, 346, 560, 700)
    assert fresh_place(CFORCE, (560, 700)) == (2560 + 573, 106, 560, 700)


def test_fresh_place_shrinks_a_window_too_big_for_the_screen():
    x, y, width, height = fresh_place(CFORCE, (2400, 1300))

    assert (width, height) == (1707 - 2 * SCREEN_MARGIN, 912 - 2 * SCREEN_MARGIN)
    assert (x, y) == (2560 + SCREEN_MARGIN, SCREEN_MARGIN)
