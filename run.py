from __future__ import annotations

import argparse
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
    parser.add_argument("--fullscreen", action="store_true")
    args = parser.parse_args()

    source, using_camera = build_source(args)
    print(f"Input: {'camera' if using_camera else 'keyboard'}")

    start(
        source,
        args.width,
        args.height,
        fullscreen=args.fullscreen,
        # Starting by holding the bar level only makes sense when there is a bar
        # to hold. On the keyboard, "level" is just nobody pressing anything.
        auto_start=using_camera,
    )


if __name__ == "__main__":
    main()
