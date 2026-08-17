"""Webcam and MediaPipe Hands, on a thread of their own.

Hand tracking runs at whatever rate the camera and the CPU manage — usually
20-30fps, and less in poor light. The game runs at 60. Doing the detection
inline would drag the game down to the camera's rate and make the steering
stutter, so capture and inference live on a background thread that publishes
its most recent result, and the game loop reads that without ever waiting.

Between camera frames the game keeps steering by the last sample, which is
correct rather than merely convenient: the player's hands really are still
where they were, and the steering filter's smoothing carries the value forward.

Two decisions worth knowing about:

  * **Hands are told apart by x position, never by MediaPipe's handedness
    label.** With both hands gripping a bar the label flips between frames, and
    a flip would mirror the steering for that frame — a violent yank in the
    wrong direction. Sorting by x cannot do that.
  * **A missing hand is not a missing frame.** `poll` returns None only when
    the hands genuinely are not there, so the game can tell "they let go" from
    "the camera is just slower than the display".
"""

from __future__ import annotations

import threading
import time

import numpy as np

from .inputs import Point, PreviewFrame, WristSample

# MediaPipe's wrist landmark. The one point per hand this game needs.
WRIST = 0

# Consecutive failed reads before we stop calling the camera healthy. Generous,
# because the occasional dropped frame is normal on cheap webcams.
_FAILURES_BEFORE_UNHEALTHY = 30


class CameraUnavailable(RuntimeError):
    """The webcam could not be opened, or the CV stack is not installed."""


class CameraInput:
    """Wrist points from a webcam, produced on a background thread."""

    def __init__(
        self,
        camera_index: int = 0,
        capture_size: tuple[int, int] = (640, 480),
        mirror: bool = True,
        model_complexity: int = 0,
        detection_confidence: float = 0.6,
        tracking_confidence: float = 0.5,
        preview_width: int = 240,
        stale_after: float = 0.4,
        startup_timeout: float = 3.0,
    ) -> None:
        cv2, mp = _import_cv_stack()

        self.mirror = mirror
        self.preview_width = preview_width
        self.stale_after = stale_after

        self._capture = cv2.VideoCapture(camera_index)
        if not self._capture.isOpened():
            self._capture.release()
            raise CameraUnavailable(
                f"Camera {camera_index} would not open."
            )
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, capture_size[0])
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, capture_size[1])
        self._demand_a_frame(startup_timeout)

        self._hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            # Complexity 0 is the light model. The heavy one buys accuracy in
            # finger poses, which this game does not look at — it reads one
            # landmark per hand — and costs framerate, which it does care about.
            model_complexity=model_complexity,
            min_detection_confidence=detection_confidence,
            min_tracking_confidence=tracking_confidence,
        )
        self._cv2 = cv2

        self._lock = threading.Lock()
        self._sample: WristSample | None = None
        self._preview: PreviewFrame | None = None
        self._stamp = 0.0
        self._frames = 0
        self._failures = 0
        self._closed = False

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    # --- the game's view -----------------------------------------------------

    def poll(self, dt: float) -> WristSample | None:
        """The most recent pair of wrists, or None if there are none.

        Deliberately returns the same sample again when the game outruns the
        camera. See the note in `inputs.InputSource` about why a repeat and an
        absence must not look alike.
        """
        with self._lock:
            sample, stamp = self._sample, self._stamp
        if sample is None or time.monotonic() - stamp > self.stale_after:
            return None
        return sample

    def preview(self) -> PreviewFrame | None:
        with self._lock:
            return self._preview

    @property
    def frames_captured(self) -> int:
        """Frames processed so far, for a tracking-rate readout at the booth."""
        with self._lock:
            return self._frames

    def close(self) -> None:
        """Stop the thread and release the camera. Safe to call more than once.

        Idempotent on purpose: shutdown can be reached from the game's `finally`
        and from an interrupt at the same time, and MediaPipe raises if its
        graph is closed twice. An exception thrown while cleaning up would bury
        whatever actually went wrong underneath it.
        """
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._capture.release()
        self._hands.close()

    @property
    def is_healthy(self) -> bool:
        """Whether the camera is still handing over frames.

        Distinct from "are there hands in them". A player whose hands are not
        found needs to move; a booth whose camera has stopped needs somebody to
        go and fix it, and telling those apart on screen saves a lot of standing
        around wondering why the game is ignoring everyone.
        """
        with self._lock:
            return self._failures < _FAILURES_BEFORE_UNHEALTHY

    # --- startup -------------------------------------------------------------

    def _demand_a_frame(self, timeout: float) -> None:
        """Insist on one real frame before declaring the camera usable.

        `isOpened()` is not good enough. On macOS a camera the user has not
        granted permission to opens perfectly happily and then fails every
        single read, which used to leave the game running with no picture, no
        error and nothing to go on.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ok, frame = self._capture.read()
            if ok and frame is not None:
                return
            time.sleep(0.05)

        self._capture.release()
        raise CameraUnavailable(
            "The camera opened but never sent a frame."
        )

    # --- the thread ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            ok, frame = self._capture.read()
            if not ok:
                # A dropped read now and then is normal. A run of them means the
                # camera has gone away, which `is_healthy` reports so the game
                # can say so rather than silently showing nothing.
                with self._lock:
                    self._failures += 1
                time.sleep(0.01)
                continue

            if self.mirror:
                # So the picture moves the way the player does. Whether this is
                # also the right steering sense is a separate question — that is
                # what config.STEER_INVERT is for.
                frame = self._cv2.flip(frame, 1)

            rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            result = self._hands.process(rgb)

            height, width = rgb.shape[:2]
            sample = wrists_from_result(result, width, height)

            with self._lock:
                self._sample = sample
                self._preview = self._shrink(rgb, sample, width)
                self._stamp = time.monotonic()
                self._frames += 1
                self._failures = 0

    def _shrink(self, rgb: np.ndarray, sample: WristSample | None, width: int) -> PreviewFrame:
        scale = self.preview_width / width
        small = self._cv2.resize(
            rgb, (self.preview_width, round(rgb.shape[0] * scale)),
            interpolation=self._cv2.INTER_AREA,
        )
        if sample is None:
            return PreviewFrame(rgb=small, left=None, right=None)
        return PreviewFrame(
            rgb=small,
            left=(sample.left[0] * scale, sample.left[1] * scale),
            right=(sample.right[0] * scale, sample.right[1] * scale),
        )


def wrists_from_result(result, width: int, height: int) -> WristSample | None:
    """Pull the two wrist points out of a MediaPipe result, in pixels.

    Returns None unless both hands were found — one wrist says nothing about
    the angle of the bar, so a single hand is no more use than none.
    """
    hands = getattr(result, "multi_hand_landmarks", None)
    if not hands or len(hands) < 2:
        return None

    points: list[Point] = [
        (hand.landmark[WRIST].x * width, hand.landmark[WRIST].y * height)
        for hand in hands[:2]
    ]
    points.sort(key=lambda point: point[0])
    return WristSample(left=points[0], right=points[1])


def _import_cv_stack():
    """Import OpenCV and MediaPipe, with an error worth reading if they are absent."""
    try:
        import cv2
        import mediapipe as mp
    except ImportError as error:
        raise CameraUnavailable(
            "The computer-vision stack is not installed.\n"
            "    uv pip install -e '.[cv]'\n"
            "Until then the game runs with --input keyboard."
        ) from error
    return cv2, mp
