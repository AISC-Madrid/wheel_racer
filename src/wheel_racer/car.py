"""Arcade car physics: auto-throttle, grass slowdown, no inertia.

Deliberately not a driving simulator. There is no throttle, no brake, no drift
and no momentum, because both of the player's hands are on the bar and because
spinning out in front of a queue is exactly the embarrassment this booth game
exists to avoid. The car always drives forward; the only thing the player can
do wrong is leave the tarmac, and the only thing that costs them is the time it
takes to get back on it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TAU = 2.0 * math.pi


@dataclass(frozen=True)
class CarTuning:
    """The four dials that decide how the car drives.

    Bundled so the physics stays free of any config import and stays trivial to
    exercise from tests with made-up values.
    """

    top_speed: float
    grass_speed: float
    accel: float
    turn_rate: float


class Car:
    """A car with a position, a heading and a speed.

    Headings are radians in screen space: 0 points along +x (right), and the
    angle grows clockwise on screen because y grows downward. Positive steering
    therefore turns right, matching the steering sign convention.
    """

    def __init__(self, x: float, y: float, heading: float = 0.0, speed: float = 0.0) -> None:
        self.x = x
        self.y = y
        self.heading = heading
        self.speed = speed

    @property
    def pose(self) -> tuple[float, float, float]:
        """``(x, y, heading)`` — what the renderer and the respawn need."""
        return self.x, self.y, self.heading

    @property
    def forward(self) -> tuple[float, float]:
        """Unit vector the car is pointing along."""
        return math.cos(self.heading), math.sin(self.heading)

    def reset(self, x: float, y: float, heading: float, speed: float = 0.0) -> None:
        """Place the car outright. Used at the start line and on respawn."""
        self.x = x
        self.y = y
        self.heading = heading
        self.speed = speed

    def update(self, steering: float, on_track: bool, dt: float, tuning: CarTuning) -> None:
        """Advance one frame. Mutates position, heading and speed in place."""
        steering = max(-1.0, min(1.0, steering))
        self._apply_throttle(on_track, dt, tuning)
        self._apply_steering(steering, dt, tuning)
        self.x += math.cos(self.heading) * self.speed * dt
        self.y += math.sin(self.heading) * self.speed * dt

    def _apply_throttle(self, on_track: bool, dt: float, tuning: CarTuning) -> None:
        """Ease toward the speed the current surface allows.

        There is no player input here at all: tarmac pulls the car up to
        top_speed, grass drags it down to grass_speed, and ``accel`` sets how
        long either takes.
        """
        target = tuning.top_speed if on_track else tuning.grass_speed
        step = tuning.accel * max(dt, 0.0)
        if self.speed < target:
            self.speed = min(target, self.speed + step)
        else:
            self.speed = max(target, self.speed - step)

    def _apply_steering(self, steering: float, dt: float, tuning: CarTuning) -> None:
        """Rotate the car, with turn radius independent of speed.

        Angular velocity is scaled by ``speed / top_speed``, which makes the
        radius ``top_speed / (steering * turn_rate)`` — the same whether the car
        is on tarmac or crawling through grass. Two things fall out of this for
        free: the grass penalty feels heavier than a bare speed drop (you cover
        the same arc but it takes longer), and a stopped car cannot pivot on the
        spot, so nobody can spin in place at the start line.
        """
        if tuning.top_speed <= 0.0:
            return
        angular_velocity = steering * tuning.turn_rate * (self.speed / tuning.top_speed)
        self.heading = (self.heading + angular_velocity * dt + math.pi) % TAU - math.pi
