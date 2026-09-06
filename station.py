"""Run the booth's link to the server.

Started automatically by `run.py`, so this is only needed by hand when the game
is not running — setting a stand up, checking the queue is draining, or working
on the board with no webcam anywhere near.

    python station.py                 # serve the kiosk, send what is queued
    python station.py --open          # ...and open the browser on it

It needs two things in a `.env` file at the top of the repository:

    WHEEL_RACER_SERVER=https://racer.example.com
    WHEEL_RACER_BOOTH_TOKEN=the-token-from-the-server

Without them it still runs: the game is still playable, results still pile up
safely on disk, and the kiosk shows the last board it managed to fetch. Only
the sending waits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wheel_racer.station import run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Hand-Wheel Racer station")
    parser.add_argument("--open", action="store_true",
                        help="open the kiosk page in a browser on startup")
    args = parser.parse_args()
    run(open_kiosk=args.open)


if __name__ == "__main__":
    main()
