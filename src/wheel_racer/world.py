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

    @property
    def predicted_lap_seconds(self) -> float:
        """Lap time for a car that keeps the racing line perfectly.

        Real times come in slower — a player is never exactly on the centreline
        and every excursion costs. Treat this as the floor, and the number to
        aim at when setting `config.TOP_SPEED`.
        """
        return self.track.total_length / self.tuning.top_speed


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
    return World(track=track, tuning=scaled_tuning(scale), scale=scale)


def scaled_tuning(scale: float) -> CarTuning:
    """The car's dials at a given scale.

    Speeds and acceleration are lengths per unit time, so they scale with the
    circuit. `turn_rate` is an angular rate and must not — angles do not care
    how big the screen is, and leaving it alone is what keeps the turn radius
    growing in step with the corners.
    """
    return CarTuning(
        top_speed=config.TOP_SPEED * scale,
        grass_speed=config.GRASS_SPEED * scale,
        accel=config.ACCEL * scale,
        turn_rate=config.TURN_RATE,
    )
