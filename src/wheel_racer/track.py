"""Track geometry: a closed centreline polyline plus a width.

One primitive does all the work. Projecting the car onto the centreline gives,
in a single operation, everything the rest of the game needs to know:

  * how far the car is from the racing line   -> on tarmac or on grass
  * how far along the line that point sits    -> lap progress, and from that,
                                                 checkpoints and lap timing
  * which way the line runs there             -> where to face a respawned car

So there is no separate collision geometry, no checkpoint objects and no
trigger volumes — those all fall out of `locate()`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

Point = tuple[float, float]

# Two centreline points closer than this are treated as the same point. Guards
# the zero-length segments that would otherwise divide by zero.
_MIN_SEGMENT_LENGTH = 1e-6


@dataclass(frozen=True)
class TrackPoint:
    """Where a world position sits relative to the track."""

    x: float
    """Closest point on the centreline."""
    y: float
    heading: float
    """Direction of travel along the centreline there, in radians."""
    distance: float
    """Perpendicular distance from the query position to the centreline."""
    progress: float
    """Arc length of that closest point, normalised to [0, 1)."""
    on_track: bool
    """Whether the query position is on tarmac."""

    @property
    def pose(self) -> tuple[float, float, float]:
        """``(x, y, heading)`` — drop a car here and it faces the right way."""
        return self.x, self.y, self.heading


class Track:
    """A closed circuit defined by its centreline and a uniform width.

    The centreline is a list of points in world (window) pixels. It is closed
    automatically: the last point joins back to the first, so do not repeat the
    first point at the end.
    """

    def __init__(self, centerline: Sequence[Point], width: float) -> None:
        if width <= 0.0:
            raise ValueError("width must be positive")

        points = _drop_repeated_points(centerline)
        if len(points) < 3:
            raise ValueError("a closed centreline needs at least 3 distinct points")

        self.width = float(width)
        self._half_width = self.width / 2.0

        # Segment i runs from _starts[i] to _starts[i] + _vectors[i], with the
        # final segment closing the loop back to the first point.
        closed = np.vstack([points, points[:1]])
        self._starts = closed[:-1]
        self._vectors = np.diff(closed, axis=0)
        self._lengths = np.hypot(self._vectors[:, 0], self._vectors[:, 1])
        self._lengths_sq = self._lengths**2
        self._headings = np.arctan2(self._vectors[:, 1], self._vectors[:, 0])

        # Arc length at the *start* of each segment, so segment i covers
        # [_offsets[i], _offsets[i] + _lengths[i]].
        self._offsets = np.concatenate([[0.0], np.cumsum(self._lengths)[:-1]])
        self._total_length = float(self._lengths.sum())

    @property
    def points(self) -> np.ndarray:
        """The centreline vertices, for the renderer. Shape ``(N, 2)``."""
        return self._starts

    @property
    def total_length(self) -> float:
        """Centreline length in pixels.

        Divide by the car's top speed for a rough lap time — the first number
        to check when authoring a circuit.
        """
        return self._total_length

    def locate(self, x: float, y: float) -> TrackPoint:
        """Project a world position onto the centreline.

        This runs once per frame for the car, so it is vectorised over every
        segment at once rather than looping in Python.
        """
        index, t, distance = self._project(x, y)
        closest = self._starts[index] + t * self._vectors[index]
        arc_length = self._offsets[index] + t * self._lengths[index]
        return TrackPoint(
            x=float(closest[0]),
            y=float(closest[1]),
            heading=float(self._headings[index]),
            distance=distance,
            progress=(arc_length / self._total_length) % 1.0,
            on_track=distance <= self._half_width,
        )

    def pose_at(self, progress: float) -> tuple[float, float, float]:
        """The pose ``(x, y, heading)`` at a given point around the lap.

        Used to place the start line and the checkpoint gates, and to put the
        car on the grid.
        """
        arc_length = (progress % 1.0) * self._total_length
        index = int(np.searchsorted(self._offsets, arc_length, side="right") - 1)
        index = max(0, min(index, len(self._lengths) - 1))
        t = (arc_length - self._offsets[index]) / self._lengths[index]
        point = self._starts[index] + t * self._vectors[index]
        return float(point[0]), float(point[1]), float(self._headings[index])

    def start_pose(self) -> tuple[float, float, float]:
        """Where the car sits on the grid, facing down the circuit."""
        return self.pose_at(0.0)

    def _project(self, x: float, y: float) -> tuple[int, float, float]:
        """Closest segment index, position along it in [0, 1], and distance."""
        relative = np.array([x, y], dtype=float) - self._starts
        t = np.einsum("ij,ij->i", relative, self._vectors) / self._lengths_sq
        np.clip(t, 0.0, 1.0, out=t)

        closest = self._starts + t[:, None] * self._vectors
        distances = np.hypot(closest[:, 0] - x, closest[:, 1] - y)

        index = int(np.argmin(distances))
        return index, float(t[index]), float(distances[index])


def _drop_repeated_points(centerline: Sequence[Point]) -> np.ndarray:
    """Remove consecutive duplicates, including a repeated closing point.

    Hand-authored centrelines pick up duplicate vertices easily, and each one
    would be a zero-length segment.
    """
    points = np.asarray(centerline, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError("centerline must be a sequence of (x, y) points")
    if len(points) == 0:
        return points

    kept = [points[0]]
    for point in points[1:]:
        if math.dist(point, kept[-1]) > _MIN_SEGMENT_LENGTH:
            kept.append(point)
    if len(kept) > 1 and math.dist(kept[-1], kept[0]) <= _MIN_SEGMENT_LENGTH:
        kept.pop()
    return np.array(kept, dtype=float)
