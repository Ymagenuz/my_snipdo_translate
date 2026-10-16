from pathlib import Path

import pytest

from app_cli import (
    AppRequest,
    CliError,
    LaunchCommand,
    delete_acknowledged_source,
    parse_cli,
    prepare_request,
)


@pytest.mark.parametrize("argv", [[], ["--show"]])
def test_no_arguments_or_explicit_show_means_show(argv):
    command = parse_cli(argv)
    assert command == LaunchCommand("show")
    prepared = prepare_request(command)
    assert prepared.request.action == "show"
    assert prepared.request.payload == {}
    assert prepared.delete_path is None


def test_startup_is_a_distinct_launch_command():
    assert parse_cli(["--startup"]) == LaunchCommand("startup")


@pytest.mark.parametrize(
    "other_args",
    [
        ["--show"],
        ["--file", "input.txt"],
        ["--image", "input.png"],
        ["--self-test", "diagnostics.json"],
        ["selected", "text"],
        ["--delete-after"],
    ],
)
def test_startup_rejects_other_actions_text_and_deletion(other_args):
    with pytest.raises(CliError):
        parse_cli(["--startup", *other_args])


def test_startup_preparation_is_harmless_and_does_not_delete_sources(tmp_path: Path):
    source = tmp_path / "input.txt"
    source.write_text("keep this text", encoding="utf-8")
    command = LaunchCommand("startup", str(source), delete_after=True)

    prepared = prepare_request(command)

    assert prepared.request.action == "show"
    assert prepared.request.payload == {}
    assert prepared.delete_path is None
    assert delete_acknowledged_source(prepared, accepted=True) is False
    assert source.read_text(encoding="utf-8") == "keep this text"
    assert command == LaunchCommand("startup", str(source), delete_after=True)


def test_startup_is_not_an_ipc_action():
    with pytest.raises(CliError, match="unsupported request action"):
        AppRequest.from_dict({
            "version": 1,
            "id": "startup-request",
            "action": "startup",
            "payload": {},
        })


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
