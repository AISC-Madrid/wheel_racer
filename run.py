"""Entry point.

    .venv/bin/python run.py                 # keyboard, for development
    .venv/bin/python run.py --fullscreen    # what the booth runs

Controls in keyboard mode: left/right (or A/D) steer, SPACE starts a run, H
pretends both hands left the bar, ESC quits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wheel_racer import config  # noqa: E402
from wheel_racer.game import start  # noqa: E402
from wheel_racer.inputs import InputSource, KeyboardInput  # noqa: E402


def build_source(name: str) -> InputSource:
    if name == "keyboard":
        return KeyboardInput()
    raise SystemExit(
        "The camera input source is not built yet — use --input keyboard.\n"
        "When it lands it will need the CV extra: uv pip install -e '.[cv]'"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Hand-Wheel Racer")
    parser.add_argument(
        "--input",
        choices=("keyboard", "camera"),
        default="keyboard",
        help="where wrist points come from (default: keyboard)",
    )
    parser.add_argument("--width", type=int, default=config.WINDOW_WIDTH)
    parser.add_argument("--height", type=int, default=config.WINDOW_HEIGHT)
    parser.add_argument("--fullscreen", action="store_true")
    args = parser.parse_args()

    start(build_source(args.input), args.width, args.height, args.fullscreen)


if __name__ == "__main__":
    main()
