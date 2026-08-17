"""Check the authored circuit without having to look at it.

Run after any edit to CONTROL_POINTS:

    .venv/bin/python tools/inspect_track.py

It answers the three questions that decide whether a circuit is playable, all
of which are easier to measure than to eyeball:

  * Is the lap the right length for the target time?
  * Are the corners open enough to take without a big steering input? A corner
    needing more than about 40% of full lock stops being a sweeper.
  * Does the track pass close enough to itself to blur two bits of tarmac into
    one, which would let players cut between them?
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wheel_racer import config, track_data  # noqa: E402
from wheel_racer.world import build_world  # noqa: E402

# Curvature is measured over arms this long. Shorter arms pick up resampling
# noise; longer ones smooth a real corner away.
CURVATURE_ARM_PX = 40.0

# Two stretches of track are "near" each other only if they are further apart
# than this along the lap — otherwise every point is near its own neighbours.
NEIGHBOUR_EXCLUSION_PX = 250.0

# A corner needing more lock than this is no longer a sweeper.
COMFORTABLE_LOCK = 0.40


def corner_radii(points: np.ndarray, spacing: float) -> np.ndarray:
    """Radius of curvature at every point, via the circle through a triple."""
    arm = max(1, round(CURVATURE_ARM_PX / spacing))
    before = np.roll(points, arm, axis=0)
    after = np.roll(points, -arm, axis=0)

    a = np.hypot(*(points - before).T)
    b = np.hypot(*(after - points).T)
    c = np.hypot(*(after - before).T)
    # Twice the triangle area, via the z of the cross product of two sides.
    u = points - before
    v = after - points
    area2 = np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0])
    return np.where(area2 > 1e-9, a * b * c / np.maximum(area2, 1e-9), np.inf)


def closest_approach(points: np.ndarray, spacing: float) -> tuple[float, int, int]:
    """Nearest distance between two stretches of track that are not neighbours."""
    deltas = points[:, None, :] - points[None, :, :]
    distances = np.hypot(deltas[..., 0], deltas[..., 1])

    count = len(points)
    index = np.arange(count)
    along_lap = np.abs(index[:, None] - index[None, :])
    along_lap = np.minimum(along_lap, count - along_lap) * spacing
    distances[along_lap <= NEIGHBOUR_EXCLUSION_PX] = np.inf

    flat = int(np.argmin(distances))
    i, j = divmod(flat, count)
    return float(distances[i, j]), i, j


def main() -> None:
    points = track_data.design_centerline()
    spacing = track_data.SAMPLE_SPACING
    world = build_world()

    length = world.track.total_length / world.scale
    print(f"Circuit          {len(track_data.CONTROL_POINTS)} control points"
          f" -> {len(points)} centreline points")
    print(f"Length           {length:,.0f} design px")
    print(f"Top speed        {config.TOP_SPEED:,.0f} px/s")
    print(f"Predicted lap    {world.predicted_lap_seconds:.1f}s"
          f"   (target {config.TARGET_LAP_SECONDS:.0f}s)")

    print()
    radii = corner_radii(points, spacing)
    full_lock_radius = config.TOP_SPEED / config.TURN_RATE
    tightest = float(radii.min())
    lock = full_lock_radius / tightest
    print(f"Full lock radius {full_lock_radius:,.0f} px")
    print(f"Tightest corner  {tightest:,.0f} px radius"
          f"  ->  {lock * 100:.0f}% of full lock")
    verdict = "sweeping" if lock <= COMFORTABLE_LOCK else "TOO TIGHT"
    print(f"                 {verdict}"
          f"  (want under {COMFORTABLE_LOCK * 100:.0f}%)")

    finite = radii[np.isfinite(radii)]
    print(f"Corner spread    median radius {np.median(finite):,.0f} px,"
          f" a quarter of the lap under {np.percentile(finite, 25):,.0f} px")

    print()
    print(f"Ways to fill a {config.TARGET_LAP_SECONDS:.0f}s run:")
    for laps in (1, 2, 3):
        speed = laps * length / config.TARGET_LAP_SECONDS
        needed = (speed / config.TURN_RATE) / tightest
        note = "" if needed <= COMFORTABLE_LOCK else "  <- corners get tight"
        print(f"  {laps} lap{'s ' if laps > 1 else '  '}"
              f" TOP_SPEED {speed:,.0f} px/s,"
              f" tightest corner at {needed * 100:.0f}% lock{note}")

    print()
    gap, i, j = closest_approach(points, spacing)
    clearance = gap - config.TRACK_WIDTH
    print(f"Closest approach {gap:,.0f} px between tarmac centres"
          f" (at {i * spacing / length:.0%} and {j * spacing / length:.0%} of the lap)")
    print(f"Grass between    {clearance:,.0f} px"
          f"   {'ok' if clearance > 40 else 'TOO NARROW — the track nearly touches itself'}")

    print()
    low = points.min(axis=0) - config.TRACK_WIDTH / 2
    high = points.max(axis=0) + config.TRACK_WIDTH / 2
    print(f"Extent           x {low[0]:,.0f}..{high[0]:,.0f}"
          f"   y {low[1]:,.0f}..{high[1]:,.0f}")
    print(f"Margin           {low[0]:,.0f} left, {track_data.DESIGN_WIDTH - high[0]:,.0f} right,"
          f" {low[1]:,.0f} top, {track_data.DESIGN_HEIGHT - high[1]:,.0f} bottom")


if __name__ == "__main__":
    main()
