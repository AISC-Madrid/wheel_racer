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
    turn_speed_response: float = 1.0
    """How much slowing down costs you in steering authority.

    Angular velocity is scaled by ``(speed / top_speed) ** turn_speed_response``:

      * ``1.0`` — turn radius is the same at every speed, like a car. Going slow
        does not tighten your line, and on grass the car answers the wheel in
        proportion to how slow it is, which can feel like the steering has
        stopped working.
      * ``0.5`` — a middle ground. Slow means a tighter line, so a car that has
        fallen off the pace can still point itself where it needs to go.
      * ``0.0`` — angular velocity ignores speed entirely, like a tank. Turn
        radius collapses at low speed.

    Below 1.0 the car turns more sharply on grass than on tarmac, which is what
    makes rejoining the circuit feel possible rather than like a punishment on
    top of a punishment.
    """


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
        """Rotate the car, with steering authority scaled by how fast it is going.

        See `CarTuning.turn_speed_response` for what the scaling means and why
        it is not simply proportional. Whatever it is set to, a car at a
        standstill does not rotate at all, so nobody can spin on the spot at the
        start line while they wait for the lights.
        """
        if tuning.top_speed <= 0.0 or self.speed <= 0.0:
            return
        authority = (self.speed / tuning.top_speed) ** tuning.turn_speed_response
        angular_velocity = steering * tuning.turn_rate * authority
        self.heading = (self.heading + angular_velocity * dt + math.pi) % TAU - math.pi
