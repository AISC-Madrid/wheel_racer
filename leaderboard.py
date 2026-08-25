"""Run the second screen.

Started separately from the game, on purpose. Either can be restarted without
the other noticing, and if this one falls over mid-afternoon the person at the
wheel never finds out.

`run.py` starts this automatically, so it is only needed by hand when the board
has been closed and wants bringing back, or when it is being worked on without
a game running.

    python leaderboard.py                 # a window on the second monitor
    python leaderboard.py --display 0     # ...on the main one
    python leaderboard.py --fullscreen    # straight to fullscreen

It opens as a window on purpose: setting a stand up means dragging windows onto
the right screens, and a fullscreen window is the one thing you cannot drag.
Put it where it belongs, then press F11.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wheel_racer.leaderboard import run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Hand-Wheel Racer leaderboard")
    parser.add_argument("--display", type=int, default=1,
                        help="which monitor (0 is the laptop; default 1)")
    parser.add_argument("--fullscreen", action="store_true",
                        help="open fullscreen instead of in a window (F11 toggles)")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    args = parser.parse_args()

    run(display=args.display, size=(args.width, args.height),
        fullscreen=args.fullscreen)


if __name__ == "__main__":
    main()
