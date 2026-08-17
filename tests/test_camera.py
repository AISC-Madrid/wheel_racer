"""Tests for turning a MediaPipe result into a pair of wrists.

The threading and the webcam are not tested here — there is no camera in CI and
a fake one would only test the fake. What *is* tested is the part that decides
which hand is which, because getting that wrong does not fail loudly: it
silently mirrors the steering, and only at the booth, and only sometimes.
"""

import time

import numpy as np
import pytest

from wheel_racer.camera import wrists_from_result
from wheel_racer.inputs import WristSample


class FakeLandmark:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class FakeHand:
    """MediaPipe hands expose 21 landmarks; only the wrist, index 0, is read."""

    def __init__(self, x: float, y: float) -> None:
        self.landmark = [FakeLandmark(x, y)] + [FakeLandmark(0.0, 0.0)] * 20


class FakeResult:
    def __init__(self, hands: list[FakeHand] | None) -> None:
        self.multi_hand_landmarks = hands


WIDTH, HEIGHT = 640, 480


class TestBothHandsPresent:
    def test_returns_wrists_in_pixels(self):
        result = FakeResult([FakeHand(0.25, 0.5), FakeHand(0.75, 0.5)])
        sample = wrists_from_result(result, WIDTH, HEIGHT)
        assert sample == WristSample(left=(160.0, 240.0), right=(480.0, 240.0))

    def test_hands_are_ordered_by_x_not_by_arrival(self):
        """MediaPipe's list order is not left-to-right, and its handedness label
        flips when the hands are close together on the bar. Either would mirror
        the steering for a frame, which is a yank in the wrong direction."""
        left_first = wrists_from_result(
            FakeResult([FakeHand(0.25, 0.4), FakeHand(0.75, 0.6)]), WIDTH, HEIGHT
        )
        right_first = wrists_from_result(
            FakeResult([FakeHand(0.75, 0.6), FakeHand(0.25, 0.4)]), WIDTH, HEIGHT
        )
        assert left_first == right_first

    def test_a_tilted_bar_keeps_its_tilt(self):
        """The left wrist high and the right low has to survive the sorting, or
        the steering angle is lost."""
        sample = wrists_from_result(
            FakeResult([FakeHand(0.3, 0.3), FakeHand(0.7, 0.7)]), WIDTH, HEIGHT
        )
        assert sample is not None
        assert sample.left[1] < sample.right[1]

    def test_hands_almost_touching_still_resolve(self):
        """Both hands gripping a short bar, which is where labels go wrong."""
        sample = wrists_from_result(
            FakeResult([FakeHand(0.51, 0.5), FakeHand(0.49, 0.5)]), WIDTH, HEIGHT
        )
        assert sample is not None
        assert sample.left[0] < sample.right[0]


class TestNotEnoughHands:
    def test_one_hand_is_no_use(self):
        """A single wrist says nothing about the angle of the bar."""
        assert wrists_from_result(FakeResult([FakeHand(0.5, 0.5)]), WIDTH, HEIGHT) is None

    def test_no_hands_detected(self):
        assert wrists_from_result(FakeResult([]), WIDTH, HEIGHT) is None

    def test_mediapipe_reports_none_when_it_finds_nothing(self):
        assert wrists_from_result(FakeResult(None), WIDTH, HEIGHT) is None

    def test_a_result_without_the_attribute_at_all(self):
        assert wrists_from_result(object(), WIDTH, HEIGHT) is None


class TestTheCaptureThread:
    """Runs the real MediaPipe pipeline over synthetic frames.

    A stubbed `VideoCapture` stands in for the webcam, so this covers the
    threading, the colour conversion, the preview scaling and the MediaPipe
    call itself — everything except the camera driver — without needing a
    camera or turning one on.
    """

    @pytest.fixture
    def camera(self, monkeypatch):
        cv2 = pytest.importorskip("cv2")
        pytest.importorskip("mediapipe")
        from wheel_racer.camera import CameraInput

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:, :, 1] = 120  # something non-black, so a blank preview is visible

        class StubCapture:
            def __init__(self, index):
                self.index = index
                self.released = False

            def isOpened(self):
                return True

            def set(self, *args):
                return True

            def read(self):
                return True, frame.copy()

            def release(self):
                self.released = True

        monkeypatch.setattr(cv2, "VideoCapture", StubCapture)
        source = CameraInput(preview_width=160)
        yield source
        source.close()

    def wait_for_a_frame(self, camera, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while camera.frames_captured == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert camera.frames_captured > 0, "capture thread produced nothing"

    def test_the_thread_captures_frames(self, camera):
        self.wait_for_a_frame(camera)

    def test_a_blank_frame_has_no_hands(self, camera):
        self.wait_for_a_frame(camera)
        assert camera.poll(1 / 60) is None

    def test_it_still_produces_a_preview_with_no_hands(self, camera):
        """The player most needs to see the camera when it is not finding them."""
        self.wait_for_a_frame(camera)
        preview = camera.preview()
        assert preview is not None
        assert not preview.has_hands

    def test_the_preview_is_scaled_to_the_requested_width(self, camera):
        self.wait_for_a_frame(camera)
        height, width = camera.preview().rgb.shape[:2]
        assert width == 160
        assert height == 120  # 4:3 preserved

    def test_a_stalled_camera_reads_as_no_hands(self, camera):
        """Unplugging the webcam must not look like a player holding still."""
        self.wait_for_a_frame(camera)
        camera._sample = WristSample(left=(0.0, 0.0), right=(10.0, 0.0))
        camera._stamp = time.monotonic() - camera.stale_after - 1.0
        assert camera.poll(1 / 60) is None

    def test_closing_releases_the_camera(self, camera):
        self.wait_for_a_frame(camera)
        camera.close()
        assert not camera._thread.is_alive()
        assert camera._capture.released

    def test_closing_twice_is_harmless(self, camera):
        """Shutdown is reachable from the game's `finally` and from an
        interrupt at once, and MediaPipe raises if closed twice — which would
        bury the real error under a cleanup error."""
        self.wait_for_a_frame(camera)
        camera.close()
        camera.close()


class TestCameraUnavailable:
    def test_a_camera_that_will_not_open_says_so(self, monkeypatch):
        cv2 = pytest.importorskip("cv2")
        pytest.importorskip("mediapipe")
        from wheel_racer.camera import CameraInput, CameraUnavailable

        class DeadCapture:
            def __init__(self, index):
                pass

            def isOpened(self):
                return False

            def release(self):
                pass

        monkeypatch.setattr(cv2, "VideoCapture", DeadCapture)
        with pytest.raises(CameraUnavailable):
            CameraInput(camera_index=7)

    def test_a_camera_that_opens_but_sends_nothing_says_so(self, monkeypatch):
        """The macOS permission signature exactly: the device opens quite
        happily and then never delivers a single frame. Left undetected this
        gives a running game with no picture and no error — which is precisely
        how it presented, and precisely why the probe exists."""
        cv2 = pytest.importorskip("cv2")
        pytest.importorskip("mediapipe")
        from wheel_racer.camera import CameraInput, CameraUnavailable

        class SilentCapture:
            def __init__(self, index):
                self.released = False

            def isOpened(self):
                return True

            def set(self, *args):
                return True

            def read(self):
                return False, None

            def release(self):
                self.released = True

        monkeypatch.setattr(cv2, "VideoCapture", SilentCapture)
        with pytest.raises(CameraUnavailable):
            CameraInput(startup_timeout=0.3)

    def test_the_probe_accepts_a_camera_that_takes_a_moment_to_wake(self, monkeypatch):
        """Webcams routinely fail the first few reads while they warm up."""
        cv2 = pytest.importorskip("cv2")
        pytest.importorskip("mediapipe")
        from wheel_racer.camera import CameraInput

        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        class SlowCapture:
            attempts = 0

            def __init__(self, index):
                pass

            def isOpened(self):
                return True

            def set(self, *args):
                return True

            def read(self):
                SlowCapture.attempts += 1
                if SlowCapture.attempts < 4:
                    return False, None
                return True, frame.copy()

            def release(self):
                pass

        monkeypatch.setattr(cv2, "VideoCapture", SlowCapture)
        source = CameraInput(startup_timeout=3.0)
        source.close()


class TestHealth:
    def test_a_working_camera_is_healthy(self, monkeypatch):
        cv2 = pytest.importorskip("cv2")
        pytest.importorskip("mediapipe")
        from wheel_racer.camera import CameraInput

        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        class Fine:
            def __init__(self, index):
                pass

            def isOpened(self):
                return True

            def set(self, *args):
                return True

            def read(self):
                return True, frame.copy()

            def release(self):
                pass

        monkeypatch.setattr(cv2, "VideoCapture", Fine)
        source = CameraInput()
        try:
            assert source.is_healthy
        finally:
            source.close()

    def test_a_camera_that_stops_mid_session_is_not_healthy(self, monkeypatch):
        """Someone kicks the USB cable halfway through the afternoon."""
        cv2 = pytest.importorskip("cv2")
        pytest.importorskip("mediapipe")
        from wheel_racer.camera import CameraInput

        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        class Flaky:
            broken = False

            def __init__(self, index):
                pass

            def isOpened(self):
                return True

            def set(self, *args):
                return True

            def read(self):
                if Flaky.broken:
                    return False, None
                return True, frame.copy()

            def release(self):
                pass

        monkeypatch.setattr(cv2, "VideoCapture", Flaky)
        source = CameraInput()
        try:
            assert source.is_healthy
            Flaky.broken = True
            deadline = time.monotonic() + 10.0
            while source.is_healthy and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not source.is_healthy
        finally:
            Flaky.broken = False
            source.close()


class TestFrameGeometry:
    @pytest.mark.parametrize("size", [(640, 480), (1280, 720), (320, 240)])
    def test_pixels_follow_the_frame_size(self, size):
        width, height = size
        sample = wrists_from_result(
            FakeResult([FakeHand(0.5, 0.5), FakeHand(1.0, 1.0)]), width, height
        )
        assert sample is not None
        assert sample.left == (width * 0.5, height * 0.5)

    def test_landmarks_outside_the_frame_are_kept(self):
        """MediaPipe extrapolates past the edge when a hand is half out of shot.
        Clamping would flatten the bar's angle exactly when it is most extreme."""
        sample = wrists_from_result(
            FakeResult([FakeHand(-0.1, 0.5), FakeHand(1.1, 0.5)]), WIDTH, HEIGHT
        )
        assert sample is not None
        assert sample.left[0] < 0
        assert sample.right[0] > WIDTH
