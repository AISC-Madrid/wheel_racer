"""The authored circuit.

The centreline is defined by a handful of control points in a fixed *design
space*, and smoothed into a circuit by a closed Catmull-Rom spline. Two reasons
for doing it this way rather than typing out a polyline:

  * A closed spline cannot fail to close, so editing the circuit is just moving
    a control point — no arithmetic to keep the ends meeting.
  * Centripetal Catmull-Rom will not fold into a cusp or a loop when the
    control points are unevenly spaced, which hand-placed points always are.

Design space is 1280x720. It is not the window: `world.build_world()` scales
the whole circuit to whatever the display happens to be, so moving to a bigger
monitor changes one number and nothing else. Run `tools/inspect_track.py` after
any edit to check the corners stayed open and the track did not touch itself.
"""

from __future__ import annotations

import numpy as np

from .track import Track

DESIGN_WIDTH = 1280
DESIGN_HEIGHT = 720

# Spacing of the resampled centreline, in design pixels. Fine enough that
# progress and grass edges are smooth, coarse enough to keep the polyline short.
SAMPLE_SPACING = 8.0

# The circuit, clockwise from the start line on the left. An outer ring with an
# indentation pushed into the top and bottom straights: the indentations add the
# lap length that a plain oval cannot reach inside one screen, and they do it
# with sweeping direction changes rather than tight corners.
CONTROL_POINTS: list[tuple[float, float]] = [
    (150.0, 470.0),   # start line, heading down-left into the first sweeper
    (225.0, 585.0),
    (390.0, 612.0),
    (600.0, 515.0),   # indentation in the bottom straight
    (810.0, 608.0),
    (1000.0, 596.0),
    (1160.0, 460.0),
    (1145.0, 255.0),
    (1000.0, 125.0),
    (810.0, 112.0),
    (615.0, 205.0),   # indentation in the top straight
    (420.0, 115.0),
    (240.0, 150.0),
    (120.0, 295.0),
]


def design_centerline() -> np.ndarray:
    """The circuit as a uniformly spaced closed polyline, in design pixels."""
    smooth = _catmull_rom_loop(np.array(CONTROL_POINTS, dtype=float))
    return _resample_uniformly(smooth, SAMPLE_SPACING)


def build_track(width: float, scale: float = 1.0, offset: tuple[float, float] = (0.0, 0.0)) -> Track:
    """Build the circuit at a given scale, in world pixels.

    Callers should go through `world.build_world()`, which works out the scale
    from the window and scales the car to match.
    """
    points = design_centerline() * scale + np.asarray(offset, dtype=float)
    return Track(points, width=width * scale)


def _catmull_rom_loop(control: np.ndarray, samples_per_span: int = 24, alpha: float = 0.5) -> np.ndarray:
    """Smooth a closed loop of control points into a dense polyline.

    ``alpha=0.5`` is the centripetal parameterisation, which is the one that
    behaves itself when the control points are not evenly spaced.
    """
    count = len(control)
    if count < 4:
        raise ValueError("a closed spline needs at least 4 control points")

    t = np.linspace(0.0, 1.0, samples_per_span, endpoint=False)
    spans = []
    for i in range(count):
        # Each span is drawn between p1 and p2, shaped by the neighbours either
        # side. Indices wrap, which is what closes the loop.
        knots = [control[(i + offset) % count] for offset in (-1, 0, 1, 2)]
        spans.append(_catmull_rom_span(knots, t, alpha))
    return np.concatenate(spans)


def _catmull_rom_span(knots: list[np.ndarray], t: np.ndarray, alpha: float) -> np.ndarray:
    """One Catmull-Rom span, between the middle two of four control points."""
    p0, p1, p2, p3 = knots

    # Knot spacing under the centripetal parameterisation.
    def next_knot(previous: float, a: np.ndarray, b: np.ndarray) -> float:
        return previous + max(float(np.linalg.norm(b - a)), 1e-9) ** alpha

    t0 = 0.0
    t1 = next_knot(t0, p0, p1)
    t2 = next_knot(t1, p1, p2)
    t3 = next_knot(t2, p2, p3)

    time = (t1 + (t2 - t1) * t)[:, None]
    a1 = (t1 - time) / (t1 - t0) * p0 + (time - t0) / (t1 - t0) * p1
    a2 = (t2 - time) / (t2 - t1) * p1 + (time - t1) / (t2 - t1) * p2
    a3 = (t3 - time) / (t3 - t2) * p2 + (time - t2) / (t3 - t2) * p3
    b1 = (t2 - time) / (t2 - t0) * a1 + (time - t0) / (t2 - t0) * a2
    b2 = (t3 - time) / (t3 - t1) * a2 + (time - t1) / (t3 - t1) * a3
    return (t2 - time) / (t2 - t1) * b1 + (time - t1) / (t2 - t1) * b2


def _resample_uniformly(points: np.ndarray, spacing: float) -> np.ndarray:
    """Re-space a closed polyline so every segment is the same length.

    The spline hands back points bunched up in the corners and spread out on
    the straights. Even spacing keeps lap progress linear in distance driven,
    which is what the checkpoint validation assumes.
    """
    closed = np.vstack([points, points[:1]])
    steps = np.hypot(*np.diff(closed, axis=0).T)
    distances = np.concatenate([[0.0], np.cumsum(steps)])
    total = float(distances[-1])

    # Fit a whole number of samples around the loop rather than stepping by
    # `spacing` and leaving a short segment where the ends meet — that last
    # segment would be the one straddling the start line, where progress
    # linearity matters most.
    count = max(3, round(total / spacing))
    wanted = np.linspace(0.0, total, count, endpoint=False)

    x = np.interp(wanted, distances, closed[:, 0])
    y = np.interp(wanted, distances, closed[:, 1])
    return np.column_stack([x, y])
