from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QMimeData, QUrl
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QTextEdit

import ui_ocr


def _image(width=120, height=40) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#FFFFFF"))
    return image


def _files(*paths: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def test_a_bare_image_is_read(qapp):
    mime = QMimeData()
    mime.setImageData(_image())

    assert ui_ocr.has_incoming_image(mime)
    image = ui_ocr.incoming_image(mime)
    assert isinstance(image, QImage)
    assert image.size() == _image().size()


def test_text_copied_with_a_picture_of_it_stays_text(qapp):
    # Word and Excel put a picture of the copied text beside it.
    mime = QMimeData()
    mime.setText("Copied paragraph")
    mime.setImageData(_image())

    assert not ui_ocr.has_incoming_image(mime)
    assert ui_ocr.incoming_image(mime) is None


def test_an_image_file_is_read_from_its_path(qapp, tmp_path: Path):
    picture = tmp_path / "capture.png"
    _image().save(str(picture))
    notes = tmp_path / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")

    assert Path(ui_ocr.incoming_image(_files(notes, picture))) == picture
    assert not ui_ocr.has_incoming_image(_files(notes))
    assert ui_ocr.incoming_image(QMimeData()) is None
    assert ui_ocr.incoming_image(None) is None


def test_the_bar_sits_above_the_text_and_goes_with_it(qapp):
    editor = QTextEdit()
    editor.resize(400, 160)
    texts = {"from": "From image", "show": "Show image", "copy": "Copy text"}
    bar = ui_ocr.SourceImageBar(editor, texts)
    editor.show()
    qapp.processEvents()
    top = editor.viewport().geometry().top()

    bar.show_image(_image())
    qapp.processEvents()

    assert bar.isVisible()
    assert editor.viewport().geometry().top() == top + ui_ocr.BAR_HEIGHT
    assert bar.geometry().bottom() < editor.viewport().geometry().top()
    assert bar.thumbnail.pixmap() is not None
    assert [bar.label.text(), bar.btn_show.text(), bar.btn_copy.text()] == [
        "From image", "Show image", "Copy text",
    ]

    bar.clear()
    assert not bar.isVisible()
    assert bar.image().isNull()
    assert editor.viewport().geometry().top() == top
    editor.close()


def test_a_small_snip_is_shown_at_its_own_size(qapp):
    editor = QTextEdit()
    editor.resize(400, 200)
    preview = ui_ocr.SnipPreview(editor)
    editor.show()
    preview.show_image(_image(60, 20))
    qapp.processEvents()

    rect = preview.image_rect()
    ratio = preview.devicePixelRatioF()
    assert rect.width() == pytest.approx(60 / max(ratio, 1.0))
    assert preview.isVisible()

    preview.show_image(_image(4000, 400))
    assert preview.image_rect().width() <= preview.width()

    preview.hide_image()
    assert not preview.isVisible()
    assert preview.image().isNull()
    editor.close()


def test_the_preview_only_sweeps_with_animations_on(qapp, motion_on):
    editor = QTextEdit()
    preview = ui_ocr.SnipPreview(editor)
    editor.show()

    preview.show_image(_image())
    assert preview.is_scanning()
    preview.hide_image()
    assert not preview.is_scanning()
    editor.close()
