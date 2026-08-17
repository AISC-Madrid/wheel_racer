"""All the pygame drawing.

The circuit is drawn once into a surface at startup and blitted every frame
after that. Nothing about it moves, and rebuilding a few hundred polygon edges
sixty times a second to get an identical picture would be the most expensive
thing in the loop by a wide margin.

The tarmac itself is two polygons, not a thick line: offsetting the centreline
by half the track width to either side gives an outer and an inner ring, and
filling the outer one with tarmac then the inner one with grass leaves a clean
band with no overlapping joints. This works because the circuit never doubles
back tighter than its own width — a property `tools/inspect_track.py` checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pygame

from .car import Car
from .inputs import PreviewFrame
from .world import World

# Muted, high-contrast against a busy outdoor scene rather than a bright
# cartoon palette that would compete with it.
GRASS = (38, 92, 58)
GRASS_SPECKLE = (46, 106, 68)
TARMAC = (58, 60, 66)
KERB = (206, 208, 214)
START_LINE = (240, 240, 245)
CHECKPOINT = (120, 190, 235)
CAR_BODY = (228, 84, 62)
CAR_CANOPY = (44, 46, 52)
CAR_SHADOW = (0, 0, 0, 70)
TEXT = (240, 242, 246)
TEXT_DIM = (168, 176, 188)
PANEL = (18, 20, 26, 190)
WARNING = (232, 116, 74)
GOOD = (126, 214, 148)

SPECKLE_COUNT = 900
SPECKLE_SEED = 7

# Car body, in design pixels, pointing along +x.
CAR_LENGTH = 30.0
CAR_WIDTH = 16.0


def format_time(seconds: float | None) -> str:
    """A lap clock. Hundredths, because tenths do not separate a leaderboard."""
    if seconds is None:
        return "--.--"
    if seconds >= 60.0:
        return f"{int(seconds // 60)}:{seconds % 60:05.2f}"
    return f"{seconds:.2f}"


@dataclass(frozen=True)
class _Ring:
    """The two edges of the tarmac, outer first."""

    outer: np.ndarray
    inner: np.ndarray


class Renderer:
    """Draws the game. Owns the prebuilt track layer and the fonts."""

    def __init__(self, screen: pygame.Surface, world: World) -> None:
        self.screen = screen
        self.world = world
        self.scale = world.scale
        self.track_layer = build_track_layer(world, screen.get_size())
        self._anchor_size: tuple[int, int] | None = None
        self._anchor: tuple[int, int] = (0, 0)

        # Monospace so the clock's digits do not shuffle sideways as they tick.
        mono = "menlo,dejavusansmono,consolas,monospace"
        self.font_clock = pygame.font.SysFont(mono, self._pt(46), bold=True)
        self.font_huge = pygame.font.SysFont(mono, self._pt(96), bold=True)
        self.font_label = pygame.font.SysFont(mono, self._pt(16), bold=True)
        self.font_body = pygame.font.SysFont(mono, self._pt(22))

    def _pt(self, design_points: int) -> int:
        return max(10, round(design_points * self.scale))

    # --- world ---------------------------------------------------------------

    def draw_world(self, car: Car) -> None:
        self.screen.blit(self.track_layer, (0, 0))
        self._draw_car(car)

    def _draw_car(self, car: Car) -> None:
        length = CAR_LENGTH * self.scale
        width = CAR_WIDTH * self.scale
        nose = length / 2.0

        body = [
            (-nose, -width / 2),
            (nose * 0.55, -width / 2),
            (nose, 0.0),
            (nose * 0.55, width / 2),
            (-nose, width / 2),
        ]
        canopy = [
            (-nose * 0.15, -width * 0.28),
            (nose * 0.45, -width * 0.22),
            (nose * 0.45, width * 0.22),
            (-nose * 0.15, width * 0.28),
        ]

        placed = _place(body, car.x, car.y, car.heading)
        shadow = _place(body, car.x + 2 * self.scale, car.y + 3 * self.scale, car.heading)

        blur = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        pygame.draw.polygon(blur, CAR_SHADOW, shadow)
        self.screen.blit(blur, (0, 0))

        pygame.draw.polygon(self.screen, CAR_BODY, placed)
        pygame.draw.polygon(self.screen, CAR_CANOPY, _place(canopy, car.x, car.y, car.heading))

    # --- hud -----------------------------------------------------------------

    def draw_hud(
        self,
        run_time: float | None,
        lap: int,
        laps_total: int,
        best: float | None,
        steering: float,
        on_track: bool,
    ) -> None:
        margin = round(18 * self.scale)

        clock = self.font_clock.render(format_time(run_time), True, TEXT)
        lap_text = self.font_label.render(f"LAP {lap}/{laps_total}", True, TEXT_DIM)
        panel_width = max(clock.get_width(), lap_text.get_width()) + 2 * margin
        panel_height = clock.get_height() + lap_text.get_height() + round(26 * self.scale)
        self._panel(pygame.Rect(margin, margin, panel_width, panel_height))
        self.screen.blit(clock, (2 * margin, margin + round(8 * self.scale)))
        self.screen.blit(lap_text, (2 * margin, margin + clock.get_height() + round(6 * self.scale)))

        best_label = self.font_label.render("BEST", True, TEXT_DIM)
        best_value = self.font_body.render(format_time(best), True, GOOD if best else TEXT_DIM)
        width = max(best_label.get_width(), best_value.get_width()) + 2 * margin
        right = self.screen.get_width() - margin - width
        self._panel(pygame.Rect(right, margin, width, best_label.get_height()
                                + best_value.get_height() + round(22 * self.scale)))
        self.screen.blit(best_label, (right + margin, margin + round(8 * self.scale)))
        self.screen.blit(best_value, (right + margin, margin + best_label.get_height()
                                      + round(12 * self.scale)))

        self._draw_steering(steering)
        if not on_track:
            self._draw_off_track_edge()

    def _draw_steering(self, steering: float) -> None:
        """A wheel-position bar.

        Mostly for the player, who needs to see that the game is reading their
        hands at all — but it is also the fastest way to tell a tracking problem
        from a driving problem while someone is at the booth.
        """
        width = round(260 * self.scale)
        height = round(10 * self.scale)
        x = (self.screen.get_width() - width) // 2
        y = self.screen.get_height() - round(34 * self.scale)

        track = pygame.Rect(x, y, width, height)
        self._panel(track.inflate(round(12 * self.scale), round(12 * self.scale)))
        pygame.draw.rect(self.screen, (70, 74, 84), track, border_radius=height // 2)

        centre = x + width // 2
        knob = round(centre + steering * width / 2)
        span = pygame.Rect(min(centre, knob), y, max(abs(knob - centre), 2), height)
        pygame.draw.rect(self.screen, CHECKPOINT, span, border_radius=height // 2)
        pygame.draw.circle(self.screen, TEXT, (knob, y + height // 2), height)

    def _draw_off_track_edge(self) -> None:
        """A warm frame around the picture while the car is on the grass.

        The speed drop is the actual penalty, but it is surprisingly easy to
        miss on a small screen — a first-timer often does not notice they have
        left the tarmac at all, only that they are suddenly slower.
        """
        thickness = round(10 * self.scale)
        glow = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        pygame.draw.rect(
            glow,
            (*WARNING, 90),
            glow.get_rect(),
            width=thickness,
            border_radius=thickness,
        )
        self.screen.blit(glow, (0, 0))

    def draw_preview(self, preview: PreviewFrame | None) -> None:
        """The camera image, small, in the bottom corner.

        Worth the screen space it costs. A player who cannot see themselves has
        no way to tell a car that will not turn from a camera that cannot see
        their hands, and neither can whoever is running the booth. The overlaid
        dots and the line between them show exactly what the game is steering
        by, which turns "it's broken" into "step into the light".
        """
        if preview is None:
            return

        height, width = preview.rgb.shape[:2]
        image = pygame.image.frombuffer(
            np.ascontiguousarray(preview.rgb).tobytes(), (width, height), "RGB"
        )

        left, top = self._preview_anchor((width, height))
        border = max(2, round(2 * self.scale))

        frame = pygame.Rect(left - border, top - border,
                            width + 2 * border, height + 2 * border)
        self._panel(frame)
        self.screen.blit(image, (left, top))

        if preview.has_hands:
            a = (left + preview.left[0], top + preview.left[1])
            b = (left + preview.right[0], top + preview.right[1])
            pygame.draw.line(self.screen, CHECKPOINT, a, b, max(2, round(3 * self.scale)))
            for point in (a, b):
                pygame.draw.circle(self.screen, TEXT, point, max(4, round(5 * self.scale)))
        else:
            label = self.font_label.render("NO HANDS", True, WARNING)
            self.screen.blit(label, (left + border, top + border))

    def _preview_anchor(self, size: tuple[int, int]) -> tuple[int, int]:
        """Find somewhere to put the preview that is not on top of the circuit.

        The obvious answer — a screen corner — is wrong here, because the
        circuit is authored to fill the window and its bulges reach into every
        corner. Hand-picking a gap would work until someone moved a control
        point, so instead this looks for one: it maps where the grass is, then
        takes the clear position furthest from the middle of the screen, which
        keeps the panel out of the way of both the car and the centre overlays.

        Worked out once per preview size and remembered.
        """
        if self._anchor_size == size:
            return self._anchor
        self._anchor_size = size
        self._anchor = self._search_for_clear_space(size)
        return self._anchor

    def _search_for_clear_space(self, size: tuple[int, int]) -> tuple[int, int]:
        width, height = size
        screen_width, screen_height = self.screen.get_size()
        margin = round(18 * self.scale)
        step = max(8, round(16 * self.scale))

        grass = self._grass_mask(step)
        columns, rows = round(width / step) + 1, round(height / step) + 1
        centre = (screen_width / 2.0, screen_height / 2.0)

        best: tuple[int, int] | None = None
        best_distance = -1.0
        for top in range(margin, screen_height - height - margin + 1, step):
            for left in range(margin, screen_width - width - margin + 1, step):
                patch = grass[top // step: top // step + rows,
                              left // step: left // step + columns]
                if not patch.all():
                    continue
                distance = math.hypot(left + width / 2 - centre[0],
                                      top + height / 2 - centre[1])
                if distance > best_distance:
                    best, best_distance = (left, top), distance

        # No clear space anywhere means the circuit fills the screen. Falling
        # back to a corner covers some tarmac, which is better than no preview.
        return best or (screen_width - width - margin, screen_height - height - margin)

    def _grass_mask(self, step: int) -> np.ndarray:
        """A coarse grid of where the tarmac is not, with room to spare."""
        screen_width, screen_height = self.screen.get_size()
        clearance = self.world.track.width / 2.0 + 8.0 * self.scale
        return np.array([
            [self.world.track.locate(float(x), float(y)).distance > clearance
             for x in range(0, screen_width + step, step)]
            for y in range(0, screen_height + step, step)
        ])

    # --- overlays ------------------------------------------------------------

    def draw_centre_message(self, title: str, subtitle: str = "", huge: bool = False) -> None:
        font = self.font_huge if huge else self.font_clock
        heading = font.render(title, True, TEXT)
        lines = [heading]
        if subtitle:
            lines.append(self.font_body.render(subtitle, True, TEXT_DIM))

        pad = round(30 * self.scale)
        gap = round(14 * self.scale)
        width = max(line.get_width() for line in lines) + 2 * pad
        height = sum(line.get_height() for line in lines) + gap * (len(lines) - 1) + 2 * pad
        rect = pygame.Rect(0, 0, width, height)
        rect.center = self.screen.get_rect().center
        self._panel(rect)

        y = rect.top + pad
        for line in lines:
            self.screen.blit(line, (rect.centerx - line.get_width() // 2, y))
            y += line.get_height() + gap

    def draw_result(self, splits: list[float], best: float | None, is_best: bool) -> None:
        total = sum(splits)
        pad = round(30 * self.scale)

        rows = [(self.font_huge.render(format_time(total), True, GOOD if is_best else TEXT), 0)]
        rows.append((self.font_label.render(
            "NEW BEST" if is_best else "YOUR TIME", True, GOOD if is_best else TEXT_DIM), 0))
        for index, split in enumerate(splits, start=1):
            rows.append((self.font_body.render(
                f"lap {index}   {format_time(split)}", True, TEXT_DIM), 0))
        if best is not None and not is_best:
            rows.append((self.font_body.render(
                f"best    {format_time(best)}", True, TEXT_DIM), 0))

        gap = round(10 * self.scale)
        width = max(row.get_width() for row, _ in rows) + 2 * pad
        height = sum(row.get_height() for row, _ in rows) + gap * (len(rows) - 1) + 2 * pad
        rect = pygame.Rect(0, 0, width, height)
        rect.center = self.screen.get_rect().center
        self._panel(rect)

        y = rect.top + pad
        for row, _ in rows:
            self.screen.blit(row, (rect.centerx - row.get_width() // 2, y))
            y += row.get_height() + gap

    def _panel(self, rect: pygame.Rect) -> None:
        """A translucent slab, so text stays readable over grass or tarmac."""
        panel = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, PANEL, panel.get_rect(), border_radius=round(10 * self.scale))
        self.screen.blit(panel, rect.topleft)


def build_track_layer(world: World, size: tuple[int, int]) -> pygame.Surface:
    """Draw the circuit once, into a surface we blit every frame."""
    layer = pygame.Surface(size)
    layer.fill(GRASS)

    ring = _tarmac_ring(world)
    pygame.draw.polygon(layer, TARMAC, ring.outer)
    pygame.draw.polygon(layer, GRASS, ring.inner)

    _speckle_the_grass(layer, world, size)

    edge = max(2, round(3 * world.scale))
    pygame.draw.polygon(layer, KERB, ring.outer, width=edge)
    pygame.draw.polygon(layer, KERB, ring.inner, width=edge)

    _draw_gates(layer, world)
    return layer


def _tarmac_ring(world: World) -> _Ring:
    """Offset the centreline either way by half the track width."""
    points = world.track.points
    half_width = world.track.width / 2.0

    tangents = np.roll(points, -1, axis=0) - np.roll(points, 1, axis=0)
    lengths = np.hypot(tangents[:, 0], tangents[:, 1])[:, None]
    normals = np.column_stack([-tangents[:, 1], tangents[:, 0]]) / lengths

    a = points + normals * half_width
    b = points - normals * half_width
    # Whichever encloses more area is the outside; which one that is depends on
    # the direction the circuit was authored in.
    return _Ring(a, b) if abs(_signed_area(a)) > abs(_signed_area(b)) else _Ring(b, a)


def _signed_area(polygon: np.ndarray) -> float:
    x, y = polygon[:, 0], polygon[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _speckle_the_grass(layer: pygame.Surface, world: World, size: tuple[int, int]) -> None:
    """Scatter flecks on the grass so it is not a flat slab of colour.

    Placed with the track's own `locate`, so they keep clear of the tarmac
    without needing a mask.
    """
    rng = np.random.default_rng(SPECKLE_SEED)
    xs = rng.uniform(0, size[0], SPECKLE_COUNT)
    ys = rng.uniform(0, size[1], SPECKLE_COUNT)
    clearance = world.track.width / 2.0 + 6.0 * world.scale

    for x, y in zip(xs, ys):
        if world.track.locate(float(x), float(y)).distance > clearance:
            pygame.draw.circle(layer, GRASS_SPECKLE, (int(x), int(y)),
                               max(1, round(2 * world.scale)))


def _draw_gates(layer: pygame.Surface, world: World) -> None:
    """The start line, and a tick at each checkpoint gate.

    Gate positions come from `laptimer.gate_progresses`, the same function the
    validator checks against, so what is drawn and what is judged cannot drift
    apart — a lap rejected for missing a gate that was never on the track would
    be impossible for a player to make sense of.

    Gates are drawn faintly and only at the edges. They are lap-validation
    machinery, not obstacles, and anything bold across the track reads as
    something to avoid.
    """
    from . import config
    from .laptimer import gate_progresses

    half_width = world.track.width / 2.0

    x, y, heading = world.track.pose_at(0.0)
    across = _across(heading)
    pygame.draw.line(
        layer, START_LINE,
        (x - across[0] * half_width, y - across[1] * half_width),
        (x + across[0] * half_width, y + across[1] * half_width),
        max(3, round(5 * world.scale)),
    )

    for progress in gate_progresses(config.NUM_CHECKPOINT_GATES):
        x, y, heading = world.track.pose_at(progress)
        across = _across(heading)
        for side in (-1, 1):
            outer = (x + across[0] * half_width * side, y + across[1] * half_width * side)
            inner = (x + across[0] * half_width * side * 0.72,
                     y + across[1] * half_width * side * 0.72)
            pygame.draw.line(layer, CHECKPOINT, inner, outer,
                             max(2, round(4 * world.scale)))


def _across(heading: float) -> tuple[float, float]:
    """Unit vector at right angles to the track, for drawing lines across it."""
    return math.cos(heading + math.pi / 2), math.sin(heading + math.pi / 2)


def _place(shape: list[tuple[float, float]], x: float, y: float,
           heading: float) -> list[tuple[float, float]]:
    """Rotate a local-space polygon by `heading` and drop it at `(x, y)`."""
    cos, sin = math.cos(heading), math.sin(heading)
    return [(x + px * cos - py * sin, y + px * sin + py * cos) for px, py in shape]
