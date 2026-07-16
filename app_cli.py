from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Action = Literal["show", "translate_text", "ocr_image"]
CommandAction = Literal["show", "text", "file", "image", "self_test"]


class CliError(ValueError):
    pass


class RaisingArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError(message)


@dataclass(frozen=True)
class LaunchCommand:
    action: CommandAction
    value: str = ""
    delete_after: bool = False


@dataclass(frozen=True)
class AppRequest:
    action: Action
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.request_id,
            "action": self.action,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AppRequest":
        if value.get("version") != 1:
            raise CliError("unsupported request version")
        action = value.get("action")
        if action not in {"show", "translate_text", "ocr_image"}:
            raise CliError("unsupported request action")
        payload = value.get("payload")
        if not isinstance(payload, dict):
            raise CliError("request payload must be an object")
        request_id = value.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise CliError("request id is required")
        return cls(action=action, payload=payload, request_id=request_id)


@dataclass(frozen=True)
class PreparedRequest:
    request: AppRequest
    delete_path: Path | None = None


def parse_cli(argv: list[str]) -> LaunchCommand:
    parser = RaisingArgumentParser(prog="SnipDoTranslate", add_help=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show", action="store_true")
    group.add_argument("--file")
    group.add_argument("--image")
    group.add_argument("--self-test")
    parser.add_argument("--delete-after", action="store_true")
    parser.add_argument("text", nargs="*")
    args = parser.parse_args(argv)

    selected = args.show or args.file is not None or args.image is not None or args.self_test is not None
    if selected and args.text:
        raise CliError("text cannot be combined with an option action")
    if args.delete_after and args.file is None and args.image is None:
        raise CliError("--delete-after requires --file or --image")
    if args.self_test is not None:
        return LaunchCommand("self_test", args.self_test)
    if args.file is not None:
        return LaunchCommand("file", args.file, args.delete_after)
    if args.image is not None:
        return LaunchCommand("image", args.image, args.delete_after)
    if args.show:
        return LaunchCommand("show")
    if args.text:
        return LaunchCommand("text", " ".join(args.text).strip())
    return LaunchCommand("show")


def prepare_request(command: LaunchCommand) -> PreparedRequest:
    if command.action == "show":
        return PreparedRequest(AppRequest("show"))
    if command.action == "text":
        if not command.value:
            raise CliError("text is empty")
        return PreparedRequest(AppRequest("translate_text", {"text": command.value}))
    if command.action == "file":
        path = Path(command.value).expanduser().resolve()
        if not path.is_file():
            raise CliError(f"file does not exist: {path}")
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise CliError(f"file is empty: {path}")
        return PreparedRequest(
            AppRequest("translate_text", {"text": text}),
            path if command.delete_after else None,
        )
    if command.action == "image":
        path = Path(command.value).expanduser().resolve()
        if not path.is_file():
            raise CliError(f"image does not exist: {path}")
        return PreparedRequest(
            AppRequest("ocr_image", {"path": str(path)}),
            path if command.delete_after else None,
        )
    raise CliError("self-test commands are not app requests")


def delete_acknowledged_source(prepared: PreparedRequest, accepted: bool) -> bool:
    if not accepted or prepared.delete_path is None:
        return False
    prepared.delete_path.unlink(missing_ok=True)
    return True
