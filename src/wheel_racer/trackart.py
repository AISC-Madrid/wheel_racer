"""Everything about the circuit that never moves, drawn once into one surface.

This is the whole picture apart from the car, the effects and the HUD: grass,
tarmac, kerbs, markings and scenery. It is built at startup and blitted each
frame, so every detail here is free at runtime no matter how much of it there
is. That is what makes dressing the track worth doing — the expensive-looking
part of the game costs nothing per frame.

Order matters more than anything else here: the ground is laid down first —
grass, then everything standing on it — and the road goes over the top. That way
nothing can end up drawn on the racing line, whatever size it is or wherever it
was placed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pygame

from . import config
from .laptimer import gate_progresses
from .logo import load_logo
from .world import World

# --- palette ---------------------------------------------------------------

GRASS = (38, 92, 58)
# Barely apart from the base green on purpose. Any more contrast and the patches
# stop reading as an uneven field and start reading as camouflage.
GRASS_DARK = (35, 86, 54)
GRASS_SPECKLE = (46, 106, 68)
TARMAC = (58, 60, 66)
TARMAC_LINE = (74, 77, 84)
KERB_RED = (196, 66, 58)
KERB_WHITE = (226, 228, 232)
EDGE = (206, 208, 214)
CHEQUER_DARK = (34, 36, 42)
CHEQUER_LIGHT = (238, 240, 244)
CHECKPOINT = (120, 190, 235)
TYRE_STACK = (30, 31, 36)
TYRE_STACK_RIM = (74, 76, 84)
TREE_SHADOW = (20, 52, 32)
TREE_CANOPY = (26, 68, 42)
TREE_CANOPY_LIT = (50, 112, 68)
TREE_TRUNK = (58, 44, 34)

# --- dimensions, in design pixels ------------------------------------------

# Kerbs go where the circuit is turning hard enough to matter. Everywhere would
# be worse: kerbs down a straight stop reading as "this is a corner".
KERB_RADIUS_THRESHOLD = 460.0
KERB_WIDTH = 11.0
KERB_BLOCK_PX = 26.0

# The dashes down the middle of the tarmac. Their whole job is speed: a plain
# surface gives the eye nothing to measure motion against, and the car ends up
# looking slower than it is.
DASH_LENGTH_PX = 26.0
DASH_GAP_PX = 34.0
DASH_WIDTH = 3.0

CHEQUER_SQUARES = 8
CHEQUER_DEPTH = 16.0

# The club logo, painted straight onto the grass beside the start line — no
# board behind it, no caption. Big enough that the mark is legible at a glance,
# which the small version inside a sign was not. Where it goes is worked out
# rather than set, so it clears the tarmac whatever angle the circuit crosses
# the start line at.
LOGO_HEIGHT = 108.0
LOGO_CLEARANCE = 12.0

SPECKLE_COUNT = 700
# Few and large. Many small ones looked like mould rather than like a field.
PATCH_COUNT = 40
PATCH_RADIUS = (30.0, 72.0)
SPECKLE_SEED = 7

SCENERY_SEED = 11
TYRE_STACKS = 12
TREES = 24
# Keep scenery this far clear of the tarmac edge, so nothing looks like it is
# about to be driven into and nothing hides the racing line.
SCENERY_CLEARANCE = 26.0
SCENERY_SPACING = 54.0

# Tyre stacks hug the track and trees stand well back, which is where they are
# on a real circuit. Scattering both at random was what made the surroundings
# read as clutter rather than as a place.
TYRE_STACK_BAND = 48.0
TREE_SETBACK = 76.0


@dataclass(frozen=True)
class _Ring:
    """The two edges of the tarmac, outer first."""

    outer: np.ndarray
    inner: np.ndarray


def build_track_layer(world: World, size: tuple[int, int]) -> pygame.Surface:
    """Draw the circuit and its surroundings once, into a surface to blit."""
    layer = pygame.Surface(size)
    layer.fill(GRASS)

    # Everything growing out of the ground goes down first, then the road is
    # laid over the top of it. Drawing the ground cover afterwards and trying to
    # keep it off the tarmac does not work: a patch is up to 72px across but
    # only its centre can be tested, so it spills over the road and sits on it.
    # Laying the road last means nothing can ever be on top of it.
    _speckle_the_grass(layer, world, size)
    _draw_scenery(layer, world, size)

    ring = _tarmac_ring(world)
    _lay_the_road(layer, ring)

    _draw_centre_dashes(layer, world)
    _draw_kerbs(layer, world, ring)

    edge = max(2, round(2 * world.scale))
    pygame.draw.polygon(layer, EDGE, ring.outer.tolist(), width=edge)
    pygame.draw.polygon(layer, EDGE, ring.inner.tolist(), width=edge)

    _draw_gates(layer, world)
    _draw_start_line(layer, world)
    return layer


# --- tarmac ----------------------------------------------------------------


def _lay_the_road(layer: pygame.Surface, ring: _Ring) -> None:
    """Fill the band between the two edges, one quad per stretch of track.

    pygame cannot fill a ring, and the two obvious workarounds are both traps.
    Filling the outer edge and then filling the inner edge with grass flattens
    everything standing inside the circuit. Filling the outer edge on a
    transparent layer and punching the inner edge back out relies on `draw`
    writing an alpha of zero, which is not guaranteed across surface formats —
    where it does not, the middle of the circuit comes out solid black.

    Quads between the edges need no transparency at all, so there is nothing
    left to depend on the display's pixel format.
    """
    count = len(ring.outer)
    for index in range(count):
        # Spanning two segments rather than one makes neighbouring quads
        # overlap, so no seam of grass can show through between them.
        far = (index + 2) % count
        pygame.draw.polygon(layer, TARMAC, [
            ring.outer[index], ring.outer[far], ring.inner[far], ring.inner[index],
        ])


def _tarmac_ring(world: World) -> _Ring:
    """Offset the centreline either way by half the track width."""
    points = world.track.points
    half_width = world.track.width / 2.0
    normals = _normals(points)

    a = points + normals * half_width
    b = points - normals * half_width
    # Whichever encloses more area is the outside; which one that is depends on
    # the direction the circuit was authored in.
    return _Ring(a, b) if abs(_signed_area(a)) > abs(_signed_area(b)) else _Ring(b, a)


def _normals(points: np.ndarray) -> np.ndarray:
    tangents = np.roll(points, -1, axis=0) - np.roll(points, 1, axis=0)
    lengths = np.hypot(tangents[:, 0], tangents[:, 1])[:, None]
    return np.column_stack([-tangents[:, 1], tangents[:, 0]]) / lengths


def _signed_area(polygon: np.ndarray) -> float:
    x, y = polygon[:, 0], polygon[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


# --- markings --------------------------------------------------------------


def _draw_kerbs(layer: pygame.Surface, world: World, ring: _Ring) -> None:
    """Red and white blocks along both edges, but only through the corners.

    More than decoration: the eye reads a striped edge streaming past as speed,
    and the stripes mark where the corner starts and ends, which is exactly the
    information a first-timer needs and currently has to guess at.
    """
    points = world.track.points
    radii = world.track.corner_radii()
    turning = radii < KERB_RADIUS_THRESHOLD * world.scale
    if not turning.any():
        return

    normals = _normals(points)
    half_width = world.track.width / 2.0
    depth = KERB_WIDTH * world.scale
    block = max(1, round(KERB_BLOCK_PX * world.scale / _spacing(world)))

    outer_is_left = ring.outer is not None and np.allclose(
        ring.outer[0], points[0] + normals[0] * half_width
    )
    side = 1.0 if outer_is_left else -1.0

    count = len(points)
    for index in range(count):
        following = (index + 1) % count
        if not (turning[index] and turning[following]):
            continue
        colour = KERB_WHITE if (index // block) % 2 else KERB_RED
        for edge_side in (side, -side):
            outer_a = points[index] + normals[index] * half_width * edge_side
            outer_b = points[following] + normals[following] * half_width * edge_side
            inner_a = points[index] + normals[index] * (half_width * edge_side
                                                        - depth * edge_side)
            inner_b = points[following] + normals[following] * (half_width * edge_side
                                                                - depth * edge_side)
            pygame.draw.polygon(layer, colour, [outer_a, outer_b, inner_b, inner_a])


def _draw_centre_dashes(layer: pygame.Surface, world: World) -> None:
    """A dashed line down the middle of the tarmac.

    Deliberately low contrast. It is there to be swept past, not read.
    """
    points = world.track.points
    spacing = _spacing(world)
    dash = max(1, round(DASH_LENGTH_PX * world.scale / spacing))
    gap = max(1, round(DASH_GAP_PX * world.scale / spacing))
    width = max(1, round(DASH_WIDTH * world.scale))

    count = len(points)
    for start in range(0, count, dash + gap):
        run = [points[i % count] for i in range(start, start + dash + 1)]
        pygame.draw.lines(layer, TARMAC_LINE, False, run, width)


def _draw_start_line(layer: pygame.Surface, world: World) -> None:
    """A chequered band across the track at progress zero."""
    x, y, heading = world.track.pose_at(0.0)
    across = _across(heading)
    along = (math.cos(heading), math.sin(heading))

    half_width = world.track.width / 2.0
    depth = CHEQUER_DEPTH * world.scale
    square = (2 * half_width) / CHEQUER_SQUARES

    for column in range(CHEQUER_SQUARES):
        for row in range(2):
            offset = -half_width + column * square
            near = depth * (row - 1)
            corners = [
                (x + across[0] * offset + along[0] * near,
                 y + across[1] * offset + along[1] * near),
                (x + across[0] * (offset + square) + along[0] * near,
                 y + across[1] * (offset + square) + along[1] * near),
                (x + across[0] * (offset + square) + along[0] * (near + depth),
                 y + across[1] * (offset + square) + along[1] * (near + depth)),
                (x + across[0] * offset + along[0] * (near + depth),
                 y + across[1] * offset + along[1] * (near + depth)),
            ]
            colour = CHEQUER_LIGHT if (column + row) % 2 else CHEQUER_DARK
            pygame.draw.polygon(layer, colour, corners)


def _draw_gates(layer: pygame.Surface, world: World) -> None:
    """A faint tick at each checkpoint gate.

    Positions come from `laptimer.gate_progresses`, the same function the
    validator checks against, so what is drawn and what is judged cannot drift
    apart. Drawn only at the edges: gates are lap-validation machinery, not
    obstacles, and anything bold across the track reads as something to avoid.
    """
    half_width = world.track.width / 2.0
    for progress in gate_progresses(config.NUM_CHECKPOINT_GATES):
        x, y, heading = world.track.pose_at(progress)
        across = _across(heading)
        for side in (-1, 1):
            outer = (x + across[0] * half_width * side, y + across[1] * half_width * side)
            inner = (x + across[0] * half_width * side * 0.72,
                     y + across[1] * half_width * side * 0.72)
            pygame.draw.line(layer, CHECKPOINT, inner, outer,
                             max(2, round(4 * world.scale)))


# --- surroundings ----------------------------------------------------------


def _speckle_the_grass(layer: pygame.Surface, world: World, size: tuple[int, int]) -> None:
    """Scatter flecks and patches so the grass is not a flat slab of colour.

    No need to keep any of it off the track: this goes down before the road
    does, so whatever lands under the tarmac is simply covered up.
    """
    rng = np.random.default_rng(SPECKLE_SEED)

    for x, y in zip(rng.uniform(0, size[0], PATCH_COUNT),
                    rng.uniform(0, size[1], PATCH_COUNT)):
        pygame.draw.circle(layer, GRASS_DARK, (int(x), int(y)),
                           round(rng.uniform(*PATCH_RADIUS) * world.scale))

    for x, y in zip(rng.uniform(0, size[0], SPECKLE_COUNT),
                    rng.uniform(0, size[1], SPECKLE_COUNT)):
        pygame.draw.circle(layer, GRASS_SPECKLE, (int(x), int(y)),
                           max(1, round(2 * world.scale)))


def _draw_scenery(layer: pygame.Surface, world: World, size: tuple[int, int]) -> None:
    """Tyre stacks, trees and a club banner, placed anywhere but the track.

    Positions are found rather than authored: candidates are rejected until one
    lands clear of the tarmac and clear of everything already placed. That way
    moving a control point in the circuit cannot leave a tree in the middle of
    the racing line.
    """
    rng = np.random.default_rng(SCENERY_SEED)
    taken: list[tuple[float, float]] = []

    _draw_club_logo(layer, world, size, taken)

    clearance = world.track.width / 2.0 + SCENERY_CLEARANCE * world.scale

    for _ in range(TYRE_STACKS):
        spot = _find_spot(world, rng, size, taken,
                          nearest=clearance,
                          furthest=clearance + TYRE_STACK_BAND * world.scale)
        if spot:
            _draw_tyre_stack(layer, world, *spot)
            taken.append(spot)

    for _ in range(TREES):
        spot = _find_spot(world, rng, size, taken,
                          nearest=clearance + TREE_SETBACK * world.scale)
        if spot:
            _draw_tree(layer, world, *spot, rng)
            taken.append(spot)


def _find_spot(world: World, rng, size: tuple[int, int],
               taken: list[tuple[float, float]],
               nearest: float, furthest: float = float("inf")) -> tuple[float, float] | None:
    """A point on the grass, the right distance out, clear of what is already there.

    The distance band is what stops the surroundings looking like objects
    sprinkled on a lawn: tyre stacks belong at the edge of the tarmac and trees
    belong back from it.
    """
    spacing = SCENERY_SPACING * world.scale
    for _ in range(80):
        x = float(rng.uniform(20, size[0] - 20))
        y = float(rng.uniform(20, size[1] - 20))
        if not nearest < world.track.locate(x, y).distance <= furthest:
            continue
        if any(math.dist((x, y), other) < spacing for other in taken):
            continue
        return x, y
    return None


def _draw_club_logo(layer: pygame.Surface, world: World, size: tuple[int, int],
                    taken: list[tuple[float, float]]) -> None:
    """The club mark, straight onto the grass, transparency and all.

    Nothing behind it and nothing beside it. A sign with the logo inset was
    smaller, darker and less recognisable than simply showing the logo.
    """
    logo = load_logo(round(LOGO_HEIGHT * world.scale))
    if logo is None:
        return

    spot = _logo_position(world, logo.get_size(), size)
    if spot is None:
        return

    layer.blit(logo, logo.get_rect(center=spot))
    # Reserve the whole footprint, not just the centre, so nothing is planted
    # on top of it.
    reach = logo.get_width() / 2.0
    taken.extend([spot, (spot[0] - reach, spot[1]), (spot[0] + reach, spot[1])])


def _logo_position(world: World, logo: tuple[int, int],
                   size: tuple[int, int]) -> tuple[int, int] | None:
    """Somewhere beside the start line where the whole mark fits on the grass.

    The logo is square to the screen while the circuit runs at whatever angle it
    likes, so its bounding box reaches back over the tarmac at exactly the
    offset that looks right on paper. Rather than pick a number that a moved
    control point would invalidate, this steps out from the line — first one
    side, then the other — until the whole rectangle is clear.
    """
    x, y, heading = world.track.pose_at(0.0)
    across = _across(heading)
    step = max(2.0, 5.0 * world.scale)
    limit = max(size) / 2.0

    for side in (1.0, -1.0):
        offset = world.track.width / 2.0
        while offset < limit:
            centre = (x + across[0] * side * offset, y + across[1] * side * offset)
            if _logo_fits(world, centre, logo, size):
                return int(centre[0]), int(centre[1])
            offset += step
    return None


def _logo_fits(world: World, centre: tuple[float, float],
               logo: tuple[int, int], size: tuple[int, int]) -> bool:
    """Whether the mark centred here is fully on screen and off the tarmac."""
    half_width, half_height = logo[0] / 2.0, logo[1] / 2.0
    left, top = centre[0] - half_width, centre[1] - half_height
    right, bottom = centre[0] + half_width, centre[1] + half_height

    margin = 8.0 * world.scale
    if left < margin or top < margin or right > size[0] - margin or bottom > size[1] - margin:
        return False

    clearance = world.track.width / 2.0 + LOGO_CLEARANCE * world.scale
    return all(
        world.track.locate(float(px), float(py)).distance > clearance
        for px in np.linspace(left, right, 9)
        for py in np.linspace(top, bottom, 5)
    )


def _draw_tyre_stack(layer: pygame.Surface, world: World, x: float, y: float) -> None:
    """Seen from above: a black ring with the hole in the middle."""
    outer = max(4, round(10 * world.scale))
    pygame.draw.circle(layer, TYRE_STACK, (int(x), int(y)), outer)
    pygame.draw.circle(layer, TYRE_STACK_RIM, (int(x), int(y)),
                       max(2, round(outer * 0.42)))


def _draw_tree(layer: pygame.Surface, world: World, x: float, y: float, rng) -> None:
    """A canopy from above, with a shadow under it to lift it off the grass.

    The shadow is what makes it a tree rather than a green circle — without it
    the canopy sits in the same plane as the field it is standing in.
    """
    canopy = round(float(rng.uniform(13, 21)) * world.scale)
    offset = max(2, round(canopy * 0.22))
    pygame.draw.circle(layer, TREE_SHADOW, (int(x + offset), int(y + offset)), canopy)
    pygame.draw.circle(layer, TREE_CANOPY, (int(x), int(y)), canopy)
    pygame.draw.circle(layer, TREE_CANOPY_LIT,
                       (int(x - canopy * 0.26), int(y - canopy * 0.26)),
                       max(2, round(canopy * 0.5)))


# --- helpers ---------------------------------------------------------------


def _spacing(world: World) -> float:
    return world.track.total_length / len(world.track.points)


def _across(heading: float) -> tuple[float, float]:
    """Unit vector at right angles to the track, for drawing lines across it."""
    return math.cos(heading + math.pi / 2), math.sin(heading + math.pi / 2)
