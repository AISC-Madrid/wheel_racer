"""Builds the circuit and the car to match whatever display we are on.

The circuit is authored once, in a fixed design space. This module scales it to
the actual window — and scales the car by exactly the same factor.

That last part is the whole point. Distances and speeds both being in pixels,
scaling only the track would make every lap slower on a big screen and every
corner tighter on a small one, and the tuning would have to be redone per
display. Scaling them together instead makes the game *identical* at any
resolution: lap time is length over speed, so a common factor cancels, and turn
radius is top_speed over turn_rate, so it scales with the circuit. Moving the
booth to a large monitor becomes a window-size change and nothing else.

The one thing a bigger screen does *not* buy is a longer lap — the circuit
looks bigger but plays exactly the same.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import config, track_data
from .car import CarTuning
from .track import Track


@dataclass(frozen=True)
class World:
    """A circuit and a car tuned to each other, ready to play."""

    track: Track
    tuning: CarTuning
    scale: float
    """Design pixels to world pixels. 1.0 at the authored 1280x720."""
    offset: tuple[float, float] = (0.0, 0.0)
    """Where the design space starts, once it is centred in the window."""

    def to_design(self, x: float, y: float) -> tuple[float, float]:
        """A world point back in design space, so it survives a resize."""
        return ((x - self.offset[0]) / self.scale, (y - self.offset[1]) / self.scale)

    def from_design(self, x: float, y: float) -> tuple[float, float]:
        """A design point placed in this world."""
        return (x * self.scale + self.offset[0], y * self.scale + self.offset[1])

    @property
    def predicted_lap_seconds(self) -> float:
        """Lap time for a car driven exactly along the centreline.

        A reference, not a bound. A good driver comes in *under* it, because
        the centreline is not the shortest way round: clipping the inside of
        every corner saves close to `2 * pi * half_width` over a lap, which on
        this circuit is about 7%. A nervous one comes in well over it. Use this
        as the number to aim `config.TOP_SPEED` at, then check real times.
        """
        return self.track.total_length / self.tuning.top_speed

    @property
    def predicted_run_seconds(self) -> float:
        """Centreline reference time for a whole attempt."""
        return self.predicted_lap_seconds * config.LAPS_PER_RUN

    @property
    def ideal_lap_seconds(self) -> float:
        """Rough best possible lap, hugging the inside of every corner.

        Approximate: the inside line only shortens the lap where the circuit
        actually turns, and this circuit turns both ways, so treat it as a
        soft lower bound on what the leaderboard will ever see.
        """
        inside_line = self.track.total_length - 2.0 * math.pi * (self.track.width / 2.0)
        return inside_line / self.tuning.top_speed


def build_world(
    window_width: int = config.WINDOW_WIDTH,
    window_height: int = config.WINDOW_HEIGHT,
) -> World:
    """Fit the authored circuit to a window, centred, preserving its shape."""
    scale = min(
        window_width / track_data.DESIGN_WIDTH,
        window_height / track_data.DESIGN_HEIGHT,
    )
    # Centre the circuit if the window is not the design aspect ratio, rather
    # than stretching it — a stretched circuit would have corners that are not
    # the radius they were authored to be.
    offset = (
        (window_width - track_data.DESIGN_WIDTH * scale) / 2.0,
        (window_height - track_data.DESIGN_HEIGHT * scale) / 2.0,
    )

    track = track_data.build_track(config.TRACK_WIDTH, scale=scale, offset=offset)
    return World(track=track, tuning=scaled_tuning(scale), scale=scale, offset=offset)


def scaled_tuning(scale: float) -> CarTuning:
    """The car's dials at a given scale.

    Speeds and acceleration are lengths per unit time, so they scale with the
    circuit. `turn_rate` is an angular rate and must not — angles do not care
    how big the screen is, and leaving it alone is what keeps the turn radius
    growing in step with the corners. `turn_speed_response` is a ratio of two
    speeds, so it is already scale-free.
    """
    return CarTuning(
        top_speed=config.TOP_SPEED * scale,
        grass_speed=config.GRASS_SPEED * scale,
        accel=config.ACCEL * scale,
        turn_rate=config.TURN_RATE,
        turn_speed_response=config.TURN_SPEED_RESPONSE,
    )
