"""Find out why the camera is not working, one layer at a time.

    .venv/bin/python tools/check_camera.py

Run this from the *same terminal or editor* you run the game from. On macOS the
camera permission belongs to that app, not to Python, so testing from somewhere
else can give a completely different answer.

It checks, in order: the libraries import, a camera opens, it actually delivers
frames, the frames are not blank, and MediaPipe can find hands in them. The
first step that fails is the one to fix.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

FRAMES_TO_SAMPLE = 60
MAX_INDEX_TO_SCAN = 3


def fail(message: str) -> None:
    print(f"\n  FAILED: {message}")


def check_imports():
    try:
        import cv2
        import mediapipe as mp
    except ImportError as error:
        fail(f"{error}\n  Install the CV extra:  uv pip install -e '.[cv]'")
        raise SystemExit(1)

    print(f"  OpenCV     {cv2.__version__}")
    print(f"  MediaPipe  {mp.__version__}")
    if not hasattr(mp, "solutions"):
        fail(
            "This MediaPipe has no `solutions` module — it is 1.0 or newer, which\n"
            "  removed the API this game uses and crashes on macOS arm64.\n"
            "  Pin it back:  uv pip install 'mediapipe>=0.10.14,<1.0'"
        )
        raise SystemExit(1)
    return cv2, mp


def scan_for_cameras(cv2) -> list[int]:
    print("\nLooking for cameras")
    found = []
    for index in range(MAX_INDEX_TO_SCAN):
        capture = cv2.VideoCapture(index)
        opened = capture.isOpened()
        delivers = False
        if opened:
            for _ in range(10):
                ok, frame = capture.read()
                if ok and frame is not None:
                    delivers = True
                    break
                time.sleep(0.05)
        capture.release()

        if delivers:
            print(f"  index {index}: works")
            found.append(index)
        elif opened:
            # The macOS permission signature: the device opens, then goes quiet.
            print(f"  index {index}: opens but sends no frames  <-- permission?")
        else:
            print(f"  index {index}: not present")
    return found


def measure(cv2, index: int):
    print(f"\nSampling {FRAMES_TO_SAMPLE} frames from camera {index}")
    capture = cv2.VideoCapture(index)
    started = time.monotonic()
    frames, blank = 0, 0
    shape = None
    while frames < FRAMES_TO_SAMPLE and time.monotonic() - started < 15.0:
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        frames += 1
        shape = frame.shape
        if frame.max() < 12:
            blank += 1
    elapsed = time.monotonic() - started
    capture.release()

    if not frames or shape is None:
        fail("no frames at all")
        return None

    print(f"  resolution {shape[1]}x{shape[0]}")
    print(f"  framerate  {frames / elapsed:.1f} fps")
    if blank > frames // 2:
        fail("frames arrive but are black — the lens cover, or another app has the camera")
    return index


def check_hands(cv2, mp, index: int) -> None:
    from wheel_racer.camera import wrists_from_result

    print("\nLooking for hands — hold both hands up in front of the camera")
    capture = cv2.VideoCapture(index)
    hands = mp.solutions.hands.Hands(
        static_image_mode=False, max_num_hands=2, model_complexity=0,
        min_detection_confidence=0.6, min_tracking_confidence=0.5,
    )

    seen = {0: 0, 1: 0, 2: 0}
    started = time.monotonic()
    while time.monotonic() - started < 8.0:
        ok, frame = capture.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB)
        result = hands.process(rgb)
        found = len(getattr(result, "multi_hand_landmarks", None) or [])
        seen[min(found, 2)] += 1

        sample = wrists_from_result(result, frame.shape[1], frame.shape[0])
        if sample is not None:
            print(f"\r  both wrists: left {sample.left[0]:6.0f},{sample.left[1]:6.0f}"
                  f"   right {sample.right[0]:6.0f},{sample.right[1]:6.0f}", end="")

    print()
    hands.close()
    capture.release()

    total = sum(seen.values()) or 1
    print(f"  no hands {seen[0] / total:.0%}  ·  one hand {seen[1] / total:.0%}"
          f"  ·  both hands {seen[2] / total:.0%}")
    if seen[2] == 0:
        fail("never saw two hands. Try better light, or move back so both are in frame.")


def main() -> None:
    print("Hand-Wheel Racer camera check")
    print("Run this from the same terminal you run the game from.\n")

    cv2, mp = check_imports()
    cameras = scan_for_cameras(cv2)
    if not cameras:
        fail(
            "no usable camera.\n"
            "  On macOS: System Settings > Privacy & Security > Camera, enable the\n"
            "  terminal or editor you ran this from, then quit and reopen it.\n"
            "  Permission is only picked up on a fresh launch, and if it was denied\n"
            "  once macOS will never ask again."
        )
        raise SystemExit(1)

    if measure(cv2, cameras[0]) is not None:
        check_hands(cv2, mp, cameras[0])
        print(f"\nAll good. Run:  .venv/bin/python run.py --input camera --camera {cameras[0]}")


if __name__ == "__main__":
    main()
