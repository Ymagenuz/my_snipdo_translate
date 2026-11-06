"""Message boxes and prompts that suit the rest of the app."""
from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox, QSizePolicy, QSpacerItem

# Room for a title such as "Check for updates" beside the window's icon and
# Close button, and for a short message to sit on one comfortable line.
MESSAGE_MIN_WIDTH = 360


def fit_message_box(box: QMessageBox, min_width: int = MESSAGE_MIN_WIDTH) -> None:
    """Keep a message box wide enough that its title isn't cut off.

    QMessageBox fixes its size to its layout's when it shows, sized to the
    message, so a short one such as "You're up to date." left the title as
    "Check for upd…". A minimum width set on the box itself is overridden;
    a spacer in its layout counts.
    """
    layout = box.layout()
    layout.addItem(
        QSpacerItem(min_width, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum),
        layout.rowCount(),
        0,
        1,
        layout.columnCount(),
    )
