"""Two wrist points -> a steering value in [-1, +1].

The player grips a rigid bar with both hands, so the line between their wrists
is the wheel: level means straight ahead, tilted means turn. We only care about
the *angle* of that line, never its length, which is what makes the reading
independent of how far the player stands from the camera.
"""

from __future__ import annotations

import math

Point = tuple[float, float]


class SteeringFilter:
    """Turns a pair of wrist landmarks into a smoothed steering value.

    Sign convention (in image space, where y grows downward): the right-hand
    wrist being *lower* than the left gives a positive value, i.e. steer right.
    That matches how a real wheel feels. Whether it matches the player depends
    on whether the camera frame is mirrored, which is what ``invert`` is for —
    flip the frame or flip this, never both.
    """

    def __init__(
        self,
        dead_zone_deg: float = 4.0,
        max_angle_deg: float = 40.0,
        smoothing_tau: float = 0.08,
        invert: bool = False,
    ) -> None:
        if max_angle_deg <= dead_zone_deg:
            raise ValueError("max_angle_deg must be greater than dead_zone_deg")
        self.dead_zone_deg = dead_zone_deg
        self.max_angle_deg = max_angle_deg
        self.smoothing_tau = smoothing_tau
        self.invert = invert
        self._value = 0.0

    @property
    def value(self) -> float:
        """The current smoothed steering value."""
        return self._value

    def reset(self) -> None:
        """Recentre the wheel. Call between runs, not during one."""
        self._value = 0.0

    def update(self, wrist_a: Point, wrist_b: Point, dt: float) -> float:
        """Feed a fresh pair of wrists and advance the smoothing by ``dt``.

        The two points may arrive in either order — we sort them by x rather
        than trusting MediaPipe's handedness label, which flips unreliably when
        the hands are close together on the bar.
        """
        return self._advance(self._raw_steering(wrist_a, wrist_b), dt)

    def hold(self) -> float:
        """Keep the last steering value, for frames where a hand was lost.

        Snapping back to centre would yank the car sideways every time tracking
        blinks, which outdoors it will.
        """
        return self._value

    def _raw_steering(self, wrist_a: Point, wrist_b: Point) -> float:
        """Unsmoothed steering from the tilt of the wrist-to-wrist line."""
        left, right = sorted((wrist_a, wrist_b), key=lambda p: p[0])
        angle_deg = math.degrees(math.atan2(right[1] - left[1], right[0] - left[0]))

        magnitude = abs(angle_deg) - self.dead_zone_deg
        if magnitude <= 0.0:
            return 0.0

        # Scale from the edge of the dead zone, not from zero, so steering eases
        # in from nothing instead of jumping as the bar leaves level.
        steering = magnitude / (self.max_angle_deg - self.dead_zone_deg)
        steering = min(steering, 1.0) * math.copysign(1.0, angle_deg)
        return -steering if self.invert else steering

    def _advance(self, target: float, dt: float) -> float:
        """Exponential smoothing with a time constant, so feel does not change
        when the camera framerate drops — which outdoors it does."""
        if self.smoothing_tau <= 0.0:
            self._value = target
        else:
            alpha = 1.0 - math.exp(-max(dt, 0.0) / self.smoothing_tau)
            self._value += (target - self._value) * alpha
        return self._value
