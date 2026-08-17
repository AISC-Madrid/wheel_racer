"""Where wrist points come from.

The game does not know or care whether a pair of wrists came from a webcam or
from the arrow keys — it only sees `WristSample`s arriving, and gaps where none
did. That indirection buys two things:

  * The whole game is playable, tunable and demonstrable without MediaPipe
    installed, which matters because the CV stack is the fragile part of the
    build and the last to be set up.
  * If the webcam fails at the booth, keyboard mode is a working fallback
    rather than a cancelled activity.

`camera.py` will add the MediaPipe source against this same protocol.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np

Point = tuple[float, float]


@dataclass(frozen=True)
class WristSample:
    """Both wrists, as seen in one frame.

    Only ever constructed when *both* are present: one wrist tells us nothing
    about the angle of the bar, so a half-sighting is the same as no sighting.
    """

    left: Point
    right: Point


@dataclass(frozen=True)
class PreviewFrame:
    """A small camera image to show the player, with what was found in it."""

    rgb: np.ndarray
    """``(height, width, 3)`` uint8."""
    left: Point | None
    """Wrist positions in this image's own pixels, or None if not seen."""
    right: Point | None

    @property
    def wrists(self) -> tuple[Point, Point] | None:
        """Both wrists together, or None unless both were seen.

        Returning the pair rather than answering a yes/no question is what lets
        a caller use them without having to re-check each one — a half-sighting
        is useless anyway, so there is never a reason to reach for one alone.
        """
        if self.left is None or self.right is None:
            return None
        return self.left, self.right

    @property
    def has_hands(self) -> bool:
        return self.wrists is not None


class InputSource(Protocol):
    """Something that produces wrist samples.

    `poll` returns ``None`` to mean "there are no wrists to steer by right
    now" — the hands were not found, or the camera has stopped producing
    frames at all. It does *not* mean "no new frame since last time": a source
    slower than the game loop repeats its most recent sample instead, because
    the two cases need opposite handling. A repeat is normal and harmless; a
    genuine absence starts the clock on abandoning the lap.
    """

    def poll(self, dt: float) -> WristSample | None:
        ...

    def preview(self) -> PreviewFrame | None:
        """A camera image to show the player, if this source has one."""
        ...

    @property
    def is_healthy(self) -> bool:
        """Whether the source is working at all.

        Separate from whether it can see hands. "Move into the light" and "the
        webcam has fallen out" need different responses from different people,
        and a booth where those look identical wastes everybody's afternoon.
        """
        ...

    def close(self) -> None:
        ...


class KeyboardInput:
    """Arrow keys pretending to be a pair of hands on a bar.

    Rather than producing a steering value directly, this fakes the two wrist
    points and lets `SteeringFilter` do its normal work, so keyboard play
    exercises the same dead zone, the same saturation and the same smoothing
    that the camera path will. Tuning done on the keyboard therefore transfers.

    Holding a key tilts the imaginary bar; releasing lets it fall back level, in
    both cases at a fixed rate, which is roughly how fast real arms move.
    """

    # Where the fake wrists sit. Arbitrary — only the angle between them is read.
    ORIGIN: Point = (320.0, 240.0)
    HALF_SPAN = 100.0

    def __init__(
        self,
        tilt_rate_deg: float = 150.0,
        centre_rate_deg: float = 220.0,
        max_tilt_deg: float = 45.0,
    ) -> None:
        self.tilt_rate_deg = tilt_rate_deg
        self.centre_rate_deg = centre_rate_deg
        self.max_tilt_deg = max_tilt_deg
        self._tilt_deg = 0.0

    def poll(self, dt: float) -> WristSample | None:
        import pygame

        keys = pygame.key.get_pressed()
        if keys[pygame.K_h]:
            # Held H pretends both hands left the bar, so the hold-last-value
            # and abandon-the-lap paths can be exercised without a webcam.
            return None

        return self.tilt(
            left=keys[pygame.K_LEFT] or keys[pygame.K_a],
            right=keys[pygame.K_RIGHT] or keys[pygame.K_d],
            dt=dt,
        )

    def tilt(self, left: bool, right: bool, dt: float) -> WristSample:
        """Advance the imaginary bar and return where the wrists ended up.

        Split out from `poll` so the behaviour can be tested without a display
        or a keyboard.
        """
        self._tilt_deg = self._next_tilt(left, right, dt)
        return self._as_wrists(self._tilt_deg)

    def preview(self) -> PreviewFrame | None:
        """No camera, nothing to show."""
        return None

    @property
    def is_healthy(self) -> bool:
        """A keyboard does not break in the interesting way."""
        return True

    def close(self) -> None:
        """Nothing to release — here to satisfy the protocol."""

    def _next_tilt(self, left: bool, right: bool, dt: float) -> float:
        if left == right:  # neither, or both: let the bar fall back level
            decay = self.centre_rate_deg * dt
            return math.copysign(max(abs(self._tilt_deg) - decay, 0.0), self._tilt_deg)

        direction = 1.0 if right else -1.0
        tilt = self._tilt_deg + direction * self.tilt_rate_deg * dt
        return max(-self.max_tilt_deg, min(self.max_tilt_deg, tilt))

    def _as_wrists(self, tilt_deg: float) -> WristSample:
        angle = math.radians(tilt_deg)
        dx = self.HALF_SPAN * math.cos(angle)
        dy = self.HALF_SPAN * math.sin(angle)
        cx, cy = self.ORIGIN
        return WristSample(left=(cx - dx, cy - dy), right=(cx + dx, cy + dy))
