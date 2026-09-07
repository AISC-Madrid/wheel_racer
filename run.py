"""Start the stand.

One command brings up the game and the station behind it. The station is what
sends results to the server and what serves the board to the screen facing the
stand — open the URL it prints in a browser, drag it onto that monitor, and
press F11.

The second screen used to be a second pygame window. It is a browser now, for
one reason: the board it shows is the same page every visitor gets on their
phone, so there is one design to keep good instead of two, and what the stand
is advertising is exactly what the room can see.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wheel_racer import config  # noqa: E402
from wheel_racer.game import start  # noqa: E402
from wheel_racer.inputs import InputSource, KeyboardInput  # noqa: E402
from wheel_racer.station.config import settings as station_settings  # noqa: E402


def open_camera(args: argparse.Namespace) -> InputSource:
    # Imported here, not at the top, so keyboard mode runs on a machine with no
    # OpenCV and no MediaPipe installed.
    from wheel_racer.camera import CameraInput

    return CameraInput(
        camera_index=args.camera,
        capture_size=config.CAMERA_CAPTURE_SIZE,
        mirror=not args.no_mirror,
        model_complexity=config.CAMERA_MODEL_COMPLEXITY,
        detection_confidence=config.CAMERA_DETECTION_CONFIDENCE,
        tracking_confidence=config.CAMERA_TRACKING_CONFIDENCE,
        preview_width=config.CAMERA_PREVIEW_WIDTH,
        stale_after=config.CAMERA_STALE_AFTER_S,
    )


def build_source(args: argparse.Namespace) -> tuple[InputSource, bool]:
    """Pick an input. Returns the source and whether it is the camera."""
    if args.input == "keyboard":
        return KeyboardInput(), False

    from wheel_racer.camera import CameraUnavailable

    try:
        return open_camera(args), True
    except CameraUnavailable as error:
        if args.input == "camera":
            # Asked for the camera by name, so a fallback would just hide the
            # problem from someone who is trying to fix it.
            raise SystemExit(str(error)) from error
        print(f"{error}\n--- falling back to keyboard mode ---\n", file=sys.stderr)
        return KeyboardInput(), False


def station_is_up(port: int) -> bool:
    """Whether something is already serving the booth on this port.

    Checked rather than assumed, because a station started by hand — to watch
    the queue drain, or to work on the board — must not be shut down by opening
    the game, and two of them cannot have the port anyway.
    """
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/booth/status", timeout=1.0):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def open_station(open_board: bool) -> subprocess.Popen | None:
    """Start the station alongside the game, unless one is already running.

    A child process rather than a thread in this one, for the reason the
    leaderboard used to be: the two stay genuinely independent, and a station
    that falls over mid-afternoon takes nothing with it. Results are on disk
    the moment they happen, so even the worst case here costs delivery time
    rather than anybody's lap.

    A failure to start is reported and then ignored. The booth's job is to let
    people drive.
    """
    script = Path(__file__).resolve().parent / "station.py"
    command = [sys.executable, str(script)]
    if open_board:
        command.append("--open")
    try:
        return subprocess.Popen(command)
    except OSError as error:
        print(f"could not start the station: {error}", file=sys.stderr)
        return None


def close_station(child: subprocess.Popen | None) -> None:
    """Ask the station to go, and wait long enough to be sure it did.

    The wait matters more than it looks: on the way out the station takes this
    stand off the live column, so a queue of five seconds here is what stops
    the public board claiming somebody is driving at a stand that has packed
    up and gone home.
    """
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="Hand-Wheel Racer")
    parser.add_argument(
        "--input",
        choices=("auto", "camera", "keyboard"),
        default="auto",
        help="where wrist points come from (default: auto — camera, else keyboard)",
    )
    parser.add_argument("--camera", type=int, default=config.CAMERA_INDEX,
                        help="which webcam, if there is more than one")
    parser.add_argument("--no-mirror", action="store_true",
                        help="do not flip the camera frame horizontally")
    parser.add_argument("--width", type=int, default=config.WINDOW_WIDTH)
    parser.add_argument("--height", type=int, default=config.WINDOW_HEIGHT)
    parser.add_argument("--fullscreen", action="store_true",
                        help="open at the desktop resolution (F11 toggles in game)")
    parser.add_argument("--no-station", action="store_true",
                        help="do not start the station (no board, no sending)")
    parser.add_argument("--open-board", action="store_true",
                        help="open the board in a browser once the station is up")
    args = parser.parse_args()

    source, using_camera = build_source(args)
    print(f"Input: {'camera' if using_camera else 'keyboard'}")

    port = station_settings().port
    station = None
    if not args.no_station:
        if station_is_up(port):
            print(f"Station: already running — board at http://127.0.0.1:{port}/")
        else:
            station = open_station(args.open_board)

    try:
        start(
            source,
            args.width,
            args.height,
            fullscreen=args.fullscreen,
            # Starting by holding the bar level only makes sense when there is a
            # bar to hold. On the keyboard, "level" is just nobody pressing
            # anything.
            auto_start=using_camera,
        )
    finally:
        # In a `finally` rather than an `atexit` hook, so the station also goes
        # when the game comes down the unhappy way — and gets its chance to
        # clear this stand off the live column on the way.
        close_station(station)


if __name__ == "__main__":
    main()
