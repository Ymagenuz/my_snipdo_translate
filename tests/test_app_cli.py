from pathlib import Path

import pytest

from app_cli import (
    CliError,
    delete_acknowledged_source,
    parse_cli,
    prepare_request,
)


def test_no_arguments_means_show():
    assert parse_cli([]).action == "show"


def test_direct_unicode_text_is_structured_request():
    prepared = prepare_request(parse_cli(["hello", "世界"]))
    assert prepared.request.action == "translate_text"
    assert prepared.request.payload == {"text": "hello 世界"}
    assert prepared.delete_path is None


def test_file_is_read_but_retained_without_delete_after(tmp_path: Path):
    source = tmp_path / "含 空格.txt"
    source.write_text("hello 世界", encoding="utf-8")
    prepared = prepare_request(parse_cli(["--file", str(source)]))
    assert prepared.request.payload == {"text": "hello 世界"}
    assert prepared.delete_path is None
    assert source.exists()


def test_file_deletes_only_after_accepted_ack(tmp_path: Path):
    source = tmp_path / "input.txt"
    source.write_text("hello", encoding="utf-8")
    prepared = prepare_request(parse_cli(["--file", str(source), "--delete-after"]))
    assert delete_acknowledged_source(prepared, accepted=False) is False
    assert source.exists()
    assert delete_acknowledged_source(prepared, accepted=True) is True
    assert not source.exists()


def test_image_request_keeps_path_for_primary_loader(tmp_path: Path):
    image = tmp_path / "截图.png"
    image.write_bytes(b"not-decoded-by-cli")
    prepared = prepare_request(parse_cli(["--image", str(image), "--delete-after"]))
    assert prepared.request.action == "ocr_image"
    assert prepared.request.payload == {"path": str(image.resolve())}
    assert prepared.delete_path == image.resolve()


@pytest.mark.parametrize(
    "argv",
    [
        ["--delete-after"],
        ["--show", "unexpected"],
        ["--file"],
        ["--image"],
        ["--unknown"],
    ],
)
def test_invalid_cli_raises_domain_error(argv):
    with pytest.raises(CliError):
        parse_cli(argv)


def test_request_json_round_trip():
    request = prepare_request(parse_cli(["hello"])).request
    restored = type(request).from_dict(request.to_dict())
    assert restored == request
