"""The two things on screen that move but are not the car.

Both exist for the same reason: at 185 pixels a second across a still picture,
the car looks slower than it is and the grass penalty looks like nothing at all.
A trail behind the car and dust thrown up off the tarmac give the eye something
to measure motion against, and turn "my time got worse" into something you can
actually see happening.

The trail earns its place twice over. The one skill this game asks for is
drawing a clean line, and a trail is that line, drawn.

Both hold their own state and age it by dt, so neither depends on the framerate.
Neither knows anything about pygame beyond drawing itself.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass

import pygame

TRAIL_COLOUR = (28, 29, 34)
DUST_COLOUR = (150, 176, 132)

# Long enough to show the shape of a corner you have just taken, short enough
# that a whole lap of it does not turn the circuit into a scribble.
TRAIL_SECONDS = 1.6
TRAIL_MAX_POINTS = 220
TRAIL_MIN_STEP = 4.0
TRAIL_WIDTH = 7.0
TRAIL_ALPHA = 90

DUST_PER_SECOND = 70.0
DUST_SECONDS = 0.65
DUST_SPEED = 55.0
DUST_SPREAD = 0.9
DUST_SIZE = 3.4
DUST_MAX = 220


@dataclass
class _Mark:
    x: float
    y: float
    age: float = 0.0


@dataclass
class _Grain:
    x: float
    y: float
    vx: float
    vy: float
    age: float = 0.0


class TyreTrail:
    """A fading line showing where the car has just been."""

    def __init__(self, seconds: float = TRAIL_SECONDS,
                 max_points: int = TRAIL_MAX_POINTS,
                 min_step: float = TRAIL_MIN_STEP) -> None:
        self.seconds = seconds
        self.min_step = min_step
        self._marks: deque[_Mark] = deque(maxlen=max_points)

    def __len__(self) -> int:
        return len(self._marks)

    def clear(self) -> None:
        """Wipe the trail. Called between runs, so one player's line does not
        appear behind the next player's car."""
        self._marks.clear()

    def update(self, x: float, y: float, dt: float) -> None:
        """Age the existing trail and, if the car has moved far enough, extend it.

        Spacing by distance rather than by frame keeps the trail the same shape
        whatever the framerate, and stops a stationary car burning a hole in it.
        """
        for mark in self._marks:
            mark.age += dt
        while self._marks and self._marks[0].age > self.seconds:
            self._marks.popleft()

        if not self._marks or math.dist((x, y), (self._marks[-1].x,
                                                 self._marks[-1].y)) >= self.min_step:
            self._marks.append(_Mark(x, y))

    def draw(self, surface: pygame.Surface, scale: float = 1.0) -> None:
        """Draw onto a transparent surface, oldest and faintest first."""
        if len(self._marks) < 2:
            return
        width = max(1, round(TRAIL_WIDTH * scale))
        marks = list(self._marks)
        for before, after in zip(marks, marks[1:]):
            fade = max(0.0, 1.0 - before.age / self.seconds)
            colour = (*TRAIL_COLOUR, int(TRAIL_ALPHA * fade))
            pygame.draw.line(surface, colour, (before.x, before.y),
                             (after.x, after.y), width)


class DustCloud:
    """Grass and dirt kicked up while the car is off the tarmac."""

    def __init__(self, seconds: float = DUST_SECONDS, per_second: float = DUST_PER_SECOND,
                 maximum: int = DUST_MAX, seed: int | None = None) -> None:
        self.seconds = seconds
        self.per_second = per_second
        self.maximum = maximum
        self._random = random.Random(seed)
        self._grains: list[_Grain] = []
        self._pending = 0.0

    def __len__(self) -> int:
        return len(self._grains)

    def clear(self) -> None:
        self._grains.clear()
        self._pending = 0.0

    def update(self, x: float, y: float, heading: float, speed: float,
               on_track: bool, dt: float, scale: float = 1.0) -> None:
        """Drift and fade what is in the air, and throw up more if off-track."""
        for grain in self._grains:
            grain.age += dt
            grain.x += grain.vx * dt
            grain.y += grain.vy * dt
        self._grains = [g for g in self._grains if g.age < self.seconds]

        if on_track or speed <= 0.0:
            self._pending = 0.0
            return
        self._emit(x, y, heading, speed, dt, scale)

    def _emit(self, x: float, y: float, heading: float, speed: float,
              dt: float, scale: float) -> None:
        """Spawn grains behind the car, thrown backwards and outwards.

        Rate is per second rather than per frame, with the fraction carried
        over, so the plume looks the same at 60fps and at 20.
        """
        self._pending += self.per_second * dt
        wanted = int(self._pending)
        self._pending -= wanted

        backwards = heading + math.pi
        for _ in range(wanted):
            if len(self._grains) >= self.maximum:
                return
            angle = backwards + self._random.uniform(-DUST_SPREAD, DUST_SPREAD)
            velocity = DUST_SPEED * scale * self._random.uniform(0.4, 1.3)
            self._grains.append(_Grain(
                x=x + math.cos(backwards) * 8.0 * scale,
                y=y + math.sin(backwards) * 8.0 * scale,
                vx=math.cos(angle) * velocity,
                vy=math.sin(angle) * velocity,
            ))

    def draw(self, surface: pygame.Surface, scale: float = 1.0) -> None:
        for grain in self._grains:
            fade = max(0.0, 1.0 - grain.age / self.seconds)
            radius = max(1, round(DUST_SIZE * scale * fade))
            pygame.draw.circle(surface, (*DUST_COLOUR, int(210 * fade)),
                               (int(grain.x), int(grain.y)), radius)
