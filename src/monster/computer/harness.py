"""Explicit one-action manual tests; never starts a model or runs arbitrary code."""

import argparse
import json
import sys
import time
from pathlib import Path

from monster.computer.client import InputSession
from monster.computer.screenshot import capture_display
from monster.computer.windows import WindowsInput


def parser():
    result = argparse.ArgumentParser(description="Monster real Windows tools: manual tests only")
    result.add_argument(
        "--delay",
        type=float,
        default=3,
        help="Seconds to focus the target before input (default 3, range 1–15)",
    )
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("displays")
    screenshot = sub.add_parser("screenshot")
    screenshot.add_argument("output", type=Path)
    screenshot.add_argument("--display", type=int, default=1, help="1-based display; 0 = all")
    for name in ("move", "click", "double-click", "right-click", "scroll", "drag"):
        command = sub.add_parser(name)
        command.add_argument("x", type=int)
        command.add_argument("y", type=int)
        if name == "scroll":
            command.add_argument("delta", type=int, help="Windows wheel units; 120 = one notch")
            command.add_argument("--horizontal", action="store_true")
        if name == "drag":
            command.add_argument("end_x", type=int)
            command.add_argument("end_y", type=int)
    text = sub.add_parser("type")
    text.add_argument("text", help="Benign sample text; do not put secrets in shell history")
    press = sub.add_parser("press")
    press.add_argument("key")
    chord = sub.add_parser("chord")
    chord.add_argument("keys", nargs="+")
    return result


def main():
    args = parser().parse_args()
    if sys.platform != "win32":
        print("Real computer tools require Windows; no input was sent.", file=sys.stderr)
        return 2
    try:
        if args.command == "displays":
            for index, display in enumerate(WindowsInput().displays(), 1):
                print(index, display)
            return 0
        if args.command == "screenshot":
            if args.output.exists():
                raise ValueError("Output already exists; choose another path.")
            observation = capture_display(args.display)
            with args.output.open("xb") as output:
                output.write(observation.png)
            print(f"Saved physical-pixel screenshot: {observation.bounds}")
            return 0
        if not 1 <= args.delay <= 15:
            raise ValueError("Delay must be between 1 and 15 seconds.")
        action = {"op": args.command}
        for field in ("x", "y", "end_x", "end_y", "delta", "horizontal", "text", "key", "keys"):
            if hasattr(args, field):
                action[field] = getattr(args, field)
        if args.command in ("double-click", "right-click"):
            action["op"] = "click"
            action["count"] = 2 if args.command == "double-click" else 1
            action["button"] = "right" if args.command == "right-click" else "left"
        if args.command == "type":
            action["op"] = "text"
        with InputSession() as session:
            print("REAL INPUT TEST. Focus a harmless target. Ctrl+Alt+X stops the worker.")
            print(
                f"One {args.command} operation starts in {args.delay:g} seconds. Release modifiers."
            )
            time.sleep(args.delay)
            session.arm()
            receipt = session.execute(action)
            print(json.dumps(receipt))  # No typed text in receipt.
        if not session.cleanup_confirmed:
            print(
                "Input cleanup could not be confirmed. Inspect held keys/buttons manually.",
                file=sys.stderr,
            )
            return 1
        return 0 if receipt["status"] == "inserted" else 1
    except (RuntimeError, ValueError, OSError, TimeoutError, KeyboardInterrupt) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
