"""Start the stand.

One command brings up both screens: the game, and the leaderboard on the other
monitor. They are still two separate processes with two separate windows — drag
either onto whichever display it belongs on, then press F11 to fill it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wheel_racer import config  # noqa: E402
from wheel_racer.game import start  # noqa: E402
from wheel_racer.inputs import InputSource, KeyboardInput  # noqa: E402


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


def open_leaderboard(display: int) -> subprocess.Popen | None:
    """Start the second screen alongside the game.

    A child process rather than a second window in this one, so the two screens
    stay genuinely independent: the leaderboard can be closed and reopened by
    hand all afternoon, and if it falls over it takes nothing with it. The only
    thing tying them together is that closing the game closes the board too,
    which is what you want at the end of the day and never notice before it.

    A failure to start is reported and then ignored. The booth's job is to let
    people drive; a missing second screen is a worse afternoon, not a lost one.
    """
    script = Path(__file__).resolve().parent / "leaderboard.py"
    try:
        return subprocess.Popen([sys.executable, str(script),
                                 "--display", str(display)])
    except OSError as error:
        print(f"could not start the leaderboard: {error}", file=sys.stderr)
        return None


def close_leaderboard(child: subprocess.Popen | None) -> None:
    """Ask the second screen to go, and wait long enough to be sure it did.

    Without the wait, quitting the game leaves the board on the monitor for as
    long as it takes to notice — which at a stand looks like the thing has
    hung. If it has not gone by then it is not going to, so it is killed
    outright rather than left behind on somebody's screen overnight.
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
    parser.add_argument("--no-leaderboard", action="store_true",
                        help="do not open the second screen")
    parser.add_argument("--leaderboard-display", type=int, default=1,
                        help="which monitor the leaderboard opens on (default 1)")
    args = parser.parse_args()

    source, using_camera = build_source(args)
    print(f"Input: {'camera' if using_camera else 'keyboard'}")

    leaderboard = None
    if not args.no_leaderboard:
        leaderboard = open_leaderboard(args.leaderboard_display)
        if leaderboard is not None:
            print("Leaderboard: opened in its own window — drag it to the "
                  "monitor, then press F11")

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
        # In a `finally` rather than an `atexit` hook, so the board also goes
        # when the game comes down the unhappy way. A booth laptop left showing
        # a frozen leaderboard next to a crashed game is a worse look than
        # showing nothing at all.
        close_leaderboard(leaderboard)


if __name__ == "__main__":
    main()
