"""The circuit, faint, behind the leaderboard — with a car lapping it.

Something has to move on this screen even when nothing is happening. A stand
runs for hours and most of those hours have nobody at the wheel, and a tower of
numbers that has not changed since the last player is a poster: the eye reads
it once and never comes back. A car going round gives it a pulse.

Two things make this close to free, which matters because the leaderboard is
sharing a laptop with the game and with MediaPipe:

  * **The circuit is drawn once.** It never moves, so it is rendered into a
    surface at startup and blitted each frame — and it replaces the flat fill
    the screen was already doing, so the per-frame cost is roughly nothing.
  * **The car is not simulated.** It has no physics, no steering and no
    collision. It is a distance along the centreline, advanced by dt, looked up
    with `Track.pose_at` — a binary search over the segment offsets. Running
    the real `Car` here would mean a second copy of the game's physics on the
    same CPU for a decoration nobody is scoring.

It laps at the current record pace, which turns the decoration into something
worth reading: that is how fast you would have to go.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

from .world import build_world

# Barely above the background. This sits underneath a screen full of text and
# its job is to imply a circuit, not to be one — any more contrast and the
# tower is being read through a road.
TARMAC = (20, 23, 31)
EDGE = (27, 31, 41)

PACE_CAR = (255, 205, 92)
"""Gold, because it is running at the leader's pace."""

CAR_LENGTH = 26.0
CAR_WIDTH = 13.0

# How far behind the car the trail reaches, as a fraction of a lap, and how
# many marks make it up. Twelve is enough to read as motion blur and few enough
# that the whole trail costs less than one text render.
TRAIL_LAP_FRACTION = 0.035
TRAIL_MARKS = 12

# Pace to lap at when nobody has set a time yet — the first minutes of the
# fair, when the board is empty and this is the only thing on screen moving.
DEFAULT_LAP_SECONDS = 15.0

# Slowest the car will ever go round, however bad the record is. A car that
# takes a minute to cross the screen has stopped being motion.
SLOWEST_LAP_SECONDS = 22.0


class Backdrop:
    """A dim circuit with a pace car on it, sized to the screen it fills."""

    def __init__(self, size: tuple[int, int], background: tuple[int, int, int]) -> None:
        self.size = size
        self.world = build_world(*size)
        self.scale = self.world.scale
        self.progress = 0.0
        self._layer = self._build_layer(background)

    def _build_layer(self, background: tuple[int, int, int]) -> pygame.Surface:
        """Draw the circuit once, into the surface that replaces the screen fill.

        An edge stroke under a slightly lighter fill, which is the cheapest way
        to make a flat band read as a road rather than as a smear.
        """
        layer = pygame.Surface(self.size)
        layer.fill(background)

        points = [(float(x), float(y)) for x, y in self.world.track.points]
        closed = points + points[:1]
        width = max(2, round(self.world.track.width))
        pygame.draw.lines(layer, EDGE, False, closed, width)
        pygame.draw.lines(layer, TARMAC, False, closed, max(1, width - 2 * self._px(2)))
        return layer

    def _px(self, design_pixels: float) -> int:
        return max(1, round(design_pixels * self.scale))

    # --- motion --------------------------------------------------------------

    def update(self, dt: float, lap_seconds: float | None = None) -> None:
        """Move the car on by dt, at the pace it is being asked to lap in.

        Distance per second rather than pixels per frame, so the car takes the
        same time round on a slow frame as on a fast one — and so a leaderboard
        running at half framerate to save CPU looks identical.
        """
        seconds = lap_seconds or DEFAULT_LAP_SECONDS
        seconds = min(max(seconds, 1.0), SLOWEST_LAP_SECONDS)
        self.progress = (self.progress + dt / seconds) % 1.0

    # --- drawing -------------------------------------------------------------

    def draw(self, screen: pygame.Surface) -> None:
        """Lay the circuit down and put the car on it."""
        screen.blit(self._layer, (0, 0))
        self._draw_trail(screen)
        self._draw_car(screen)

    def _draw_trail(self, screen: pygame.Surface) -> None:
        """A fading smear along the road behind the car.

        Sampled backwards along the centreline rather than remembered frame by
        frame, so it holds no state, cannot drift out of step with the car, and
        looks the same however long the screen has been up.
        """
        for index in range(TRAIL_MARKS, 0, -1):
            fade = 1.0 - index / (TRAIL_MARKS + 1)
            behind = self.progress - TRAIL_LAP_FRACTION * index / TRAIL_MARKS
            x, y, _ = self.world.track.pose_at(behind)
            radius = max(1, round(CAR_WIDTH * self.scale * 0.45 * fade))
            pygame.draw.circle(screen, blend(TARMAC, PACE_CAR, fade * 0.55),
                               (int(x), int(y)), radius)

    def _draw_car(self, screen: pygame.Surface) -> None:
        x, y, heading = self.world.track.pose_at(self.progress)
        nose = CAR_LENGTH * self.scale / 2.0
        half = CAR_WIDTH * self.scale / 2.0
        body = [
            (-nose, -half * 0.8), (nose * 0.6, -half), (nose, -half * 0.45),
            (nose, half * 0.45), (nose * 0.6, half), (-nose, half * 0.8),
        ]
        cos, sin = math.cos(heading), math.sin(heading)
        pygame.draw.polygon(screen, PACE_CAR, [
            (x + px * cos - py * sin, y + px * sin + py * cos) for px, py in body
        ])


def blend(colour: tuple[int, int, int], target: tuple[int, int, int],
          amount: float) -> tuple[int, int, int]:
    """Mix two colours, for fading without needing an alpha layer.

    Blending against the known road colour instead of compositing means the
    trail is a handful of opaque circles rather than a full-screen surface with
    an alpha channel — which, sixty times a second next to a game doing hand
    tracking, is the difference worth having.
    """
    amount = max(0.0, min(1.0, amount))
    return tuple(round(a + (b - a) * amount) for a, b in zip(colour, target))


def lap_pace(seconds: float | None) -> float:
    """The pace to lap the backdrop at, given the record.

    Kept out of `update` so the caller can hand over whatever it likes — a
    record, a default, or nothing — without the drawing code knowing where
    times come from.
    """
    return seconds if seconds and seconds > 0 else DEFAULT_LAP_SECONDS


def circuit_bounds(size: tuple[int, int]) -> tuple[float, float, float, float]:
    """Where the circuit sits in a screen of this size, for tests to check."""
    world = build_world(*size)
    points = np.asarray(world.track.points, dtype=float)
    return (points[:, 0].min(), points[:, 1].min(),
            points[:, 0].max(), points[:, 1].max())
