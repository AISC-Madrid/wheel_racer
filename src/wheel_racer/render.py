"""Drawing the things that change: the car, the effects, the HUD.

The circuit itself is not here — it never moves, so it is drawn once into a
surface by `trackart` and blitted. What is left is per-frame work, and there is
one rule about it: no full-screen surface is allocated inside the loop. The
trail, the dust and the car's shadow all need alpha, so they share a single
scratch layer that is cleared and reused, and the off-track warning frame is
built once and kept.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

from .car import Car
from .effects import DustCloud, TyreTrail
from .inputs import PreviewFrame
from .login import LoginForm
from .trackart import CHEQUER_DARK, CHEQUER_LIGHT, CHECKPOINT, build_track_layer
from .world import World

TEXT = (240, 242, 246)
TEXT_DIM = (168, 176, 188)
PANEL = (18, 20, 26, 190)
WARNING = (232, 116, 74)
GOOD = (126, 214, 148)

FIELD_BOX = (30, 33, 41)
FIELD_BOX_FOCUSED = (44, 50, 62)
FIELD_EDGE = (72, 78, 92)
FIELD_EDGE_FOCUSED = CHECKPOINT

# The sign-in panel, in design pixels.
LOGIN_WIDTH = 560.0
FLAG_HEIGHT = 18.0
FLAG_SQUARES = 16
FIELD_HEIGHT = 44.0

CAR_BODY = (228, 84, 62)
CAR_BODY_DARK = (176, 58, 42)
CAR_CANOPY = (38, 42, 52)
CAR_GLASS = (128, 176, 210)
CAR_WHEEL = (26, 27, 31)
CAR_SHADOW = (0, 0, 0, 80)

# Car body, in design pixels, pointing along +x.
CAR_LENGTH = 34.0
CAR_WIDTH = 18.0
WHEEL_LENGTH = 10.0
WHEEL_WIDTH = 5.0
# How far the front wheels visibly turn at full lock. Exaggerated well past
# anything the car actually does, because a wheel that moves two degrees may as
# well not move: this is the player's confirmation that their hands registered.
MAX_WHEEL_DEG = 30.0


def format_time(seconds: float | None) -> str:
    """A lap clock. Hundredths, because tenths do not separate a leaderboard."""
    if seconds is None:
        return "--.--"
    if seconds >= 60.0:
        return f"{int(seconds // 60)}:{seconds % 60:05.2f}"
    return f"{seconds:.2f}"


class Renderer:
    """Draws the game. Owns the prebuilt track layer, the fonts and the scratch."""

    def __init__(self, screen: pygame.Surface, world: World) -> None:
        self.screen = screen
        self.world = world
        self.scale = world.scale
        self.track_layer = build_track_layer(world, screen.get_size())

        # One transparent layer, cleared and reused every frame for everything
        # that needs alpha. Allocating these per frame was several megabytes a
        # second of churn for no reason.
        #
        # The explicit 32 matters: SRCALPHA on its own can inherit a display
        # format with no alpha channel, and then clearing to (0, 0, 0, 0) gives
        # opaque black rather than nothing, which blits the whole picture out.
        self._scratch = pygame.Surface(screen.get_size(), pygame.SRCALPHA, 32)
        self._edge_glow: pygame.Surface | None = None
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

    def draw_world(self, car: Car, steering: float = 0.0,
                   trail: TyreTrail | None = None,
                   dust: DustCloud | None = None) -> None:
        self.screen.blit(self.track_layer, (0, 0))

        self._scratch.fill((0, 0, 0, 0))
        if trail is not None:
            trail.draw(self._scratch, self.scale)
        if dust is not None:
            dust.draw(self._scratch, self.scale)
        self._draw_shadow(car)
        self.screen.blit(self._scratch, (0, 0))

        self._draw_car(car, steering)

    def _car_body(self) -> list[tuple[float, float]]:
        nose = CAR_LENGTH * self.scale / 2.0
        half = CAR_WIDTH * self.scale / 2.0
        return [
            (-nose, -half * 0.86),
            (-nose * 0.7, -half),
            (nose * 0.55, -half),
            (nose, -half * 0.5),
            (nose, half * 0.5),
            (nose * 0.55, half),
            (-nose * 0.7, half),
            (-nose, half * 0.86),
        ]

    def _draw_shadow(self, car: Car) -> None:
        offset = 3.0 * self.scale
        pygame.draw.polygon(self._scratch, CAR_SHADOW,
                            _place(self._car_body(), car.x + offset, car.y + offset,
                                   car.heading))

    def _draw_car(self, car: Car, steering: float) -> None:
        nose = CAR_LENGTH * self.scale / 2.0
        half = CAR_WIDTH * self.scale / 2.0
        wheel_angle = math.radians(MAX_WHEEL_DEG) * max(-1.0, min(1.0, steering))

        # Wheels first, so the body sits over their inner ends and only the part
        # that should stick out does.
        for along, turn in ((nose * 0.58, wheel_angle), (-nose * 0.6, 0.0)):
            for side in (-1.0, 1.0):
                self._draw_wheel(car, along, side * (half + 1.0 * self.scale), turn)

        pygame.draw.polygon(self.screen, CAR_BODY,
                            _place(self._car_body(), car.x, car.y, car.heading))
        pygame.draw.polygon(self.screen, CAR_BODY_DARK,
                            _place(self._car_body(), car.x, car.y, car.heading),
                            width=max(1, round(2 * self.scale)))

        canopy = [
            (-nose * 0.25, -half * 0.62),
            (nose * 0.3, -half * 0.5),
            (nose * 0.3, half * 0.5),
            (-nose * 0.25, half * 0.62),
        ]
        pygame.draw.polygon(self.screen, CAR_CANOPY,
                            _place(canopy, car.x, car.y, car.heading))
        windscreen = [
            (nose * 0.16, -half * 0.44),
            (nose * 0.32, -half * 0.36),
            (nose * 0.32, half * 0.36),
            (nose * 0.16, half * 0.44),
        ]
        pygame.draw.polygon(self.screen, CAR_GLASS,
                            _place(windscreen, car.x, car.y, car.heading))

    def _draw_wheel(self, car: Car, along: float, across: float, turn: float) -> None:
        length = WHEEL_LENGTH * self.scale / 2.0
        width = WHEEL_WIDTH * self.scale / 2.0
        shape = [(-length, -width), (length, -width), (length, width), (-length, width)]
        # Turn the wheel about its own hub, put the hub on the car, then take the
        # whole thing round to the car's heading.
        hub = _offset(_rotate(shape, turn), along, across)
        pygame.draw.polygon(self.screen, CAR_WHEEL,
                            _place(hub, car.x, car.y, car.heading))

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

        best_label = self.font_label.render("BEST LAP", True, TEXT_DIM)
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
        left the tarmac at all, only that they are suddenly slower. Built once
        and kept, since it is the same every time.
        """
        if self._edge_glow is None:
            thickness = round(10 * self.scale)
            glow = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA, 32)
            pygame.draw.rect(glow, (*WARNING, 90), glow.get_rect(),
                             width=thickness, border_radius=thickness)
            self._edge_glow = glow
        self.screen.blit(self._edge_glow, (0, 0))

    # --- camera preview ------------------------------------------------------

    def draw_preview(self, preview: PreviewFrame | None) -> None:
        """The camera image, small, clear of the circuit.

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

        wrists = preview.wrists
        if wrists is None:
            label = self.font_label.render("NO HANDS", True, WARNING)
            self.screen.blit(label, (left + border, top + border))
            return

        (left_x, left_y), (right_x, right_y) = wrists
        a = (left + left_x, top + left_y)
        b = (left + right_x, top + right_y)
        pygame.draw.line(self.screen, CHECKPOINT, a, b, max(2, round(3 * self.scale)))
        for point in (a, b):
            pygame.draw.circle(self.screen, TEXT, point, max(4, round(5 * self.scale)))

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

    def draw_login(self, form: LoginForm, headline: str, subhead: str = "",
                   hint: str = "", celebrate: bool = False, replay: bool = False) -> None:
        """The sign-in panel, over whatever the game is showing behind it.

        The same panel does duty before the first run and after every one: the
        only difference is the headline above it, which is the game's name to
        begin with and the player's time afterwards. Keeping it one panel means
        handing the machine to the next person is the same few keystrokes
        wherever you are in the evening.
        """
        pad = round(26 * self.scale)
        flag = round(FLAG_HEIGHT * self.scale)
        gap = round(12 * self.scale)

        title = (self.font_huge if celebrate else self.font_clock).render(
            headline, True, GOOD if celebrate else TEXT)
        under = self.font_label.render(subhead, True, TEXT_DIM) if subhead else None
        footer = self.font_label.render(hint, True, TEXT_DIM) if hint else None
        message = self.font_body.render(form.error, True, WARNING) if form.error else None

        width = self._login_width(form, headline, subhead, hint, celebrate, replay)
        icon = self._replay_icon_size() if replay else 0
        icon_gap = round(9 * self.scale) if replay else 0
        box_height = round(FIELD_HEIGHT * self.scale)
        label_height = self.font_label.get_height()
        rows = len(form.fields) * (label_height + box_height + gap)

        height = (2 * flag + 2 * pad + title.get_height() + gap + rows
                  + (under.get_height() + gap if under else 0)
                  + (message.get_height() + gap if message else 0)
                  + (footer.get_height() if footer else 0))

        panel = pygame.Rect(0, 0, width, height)
        panel.center = self.screen.get_rect().center
        self._panel(panel)
        self._chequered_strip(pygame.Rect(panel.left, panel.top, width, flag))
        self._chequered_strip(pygame.Rect(panel.left, panel.bottom - flag, width, flag))

        y = panel.top + flag + pad
        self.screen.blit(title, (panel.centerx - title.get_width() // 2, y))
        y += title.get_height() + gap
        if under:
            self.screen.blit(under, (panel.centerx - under.get_width() // 2, y))
            y += under.get_height() + gap

        for index, box in enumerate(form.fields):
            focused = index == form.focused
            label = self.font_label.render(box.label, True,
                                           FIELD_EDGE_FOCUSED if focused else TEXT_DIM)
            self.screen.blit(label, (panel.left + pad, y))
            y += label_height

            rect = pygame.Rect(panel.left + pad, y, width - 2 * pad, box_height)
            pygame.draw.rect(self.screen, FIELD_BOX_FOCUSED if focused else FIELD_BOX,
                             rect, border_radius=round(6 * self.scale))
            pygame.draw.rect(self.screen, FIELD_EDGE_FOCUSED if focused else FIELD_EDGE,
                             rect, width=max(1, round(2 * self.scale)),
                             border_radius=round(6 * self.scale))

            value = self.font_body.render(box.value, True, TEXT)
            inset = round(12 * self.scale)
            self.screen.blit(value, (rect.left + inset,
                                     rect.centery - value.get_height() // 2))
            if focused and self._caret_is_showing():
                caret = rect.left + inset + value.get_width() + round(2 * self.scale)
                pygame.draw.line(self.screen, TEXT,
                                 (caret, rect.top + inset // 2),
                                 (caret, rect.bottom - inset // 2),
                                 max(1, round(2 * self.scale)))
            y += box_height + gap

        if message:
            self.screen.blit(message, (panel.centerx - message.get_width() // 2, y))
            y += message.get_height() + gap
        if footer:
            block = footer.get_width() + icon + icon_gap
            left = panel.centerx - block // 2
            if replay:
                self._draw_replay_icon(
                    (left + icon // 2, y + footer.get_height() // 2), icon, TEXT)
            self.screen.blit(footer, (left + icon + icon_gap, y))

    def _replay_icon_size(self) -> int:
        """Half again the height of the line it sits beside, so it reads as a
        symbol rather than as a full stop."""
        return round(self.font_label.get_height() * 1.5)

    def _draw_replay_icon(self, centre: tuple[int, int], size: int,
                          colour: tuple[int, int, int]) -> None:
        """A circular arrow, drawn rather than typed.

        The obvious character for this is not in every monospace font, and a
        missing glyph would show as an empty box on the one screen every player
        sees. Fifteen lines of arc and triangle cannot go missing.
        """
        thickness = max(2, round(size * 0.15))
        # The arc is inset so the head, which is wider than the stroke, still
        # fits inside the space the icon was measured to take.
        radius = size / 2.0 - thickness
        box = pygame.Rect(0, 0, round(radius * 2), round(radius * 2))
        box.center = centre

        # A gap at the top right, where the head goes. pygame measures arc
        # angles anticlockwise from three o'clock.
        opening = 1.0
        pygame.draw.arc(self.screen, colour, box, opening, 2.0 * math.pi, thickness)

        # Head at the open end, pointing the way the arrow travels: clockwise,
        # which is the direction of decreasing angle.
        tip_x = centre[0] + radius * math.cos(opening)
        tip_y = centre[1] - radius * math.sin(opening)
        along = (math.sin(opening), math.cos(opening))
        across = (-along[1], along[0])
        reach = size * 0.30
        spread = size * 0.17
        pygame.draw.polygon(self.screen, colour, [
            (tip_x + along[0] * reach, tip_y + along[1] * reach),
            (tip_x + across[0] * spread, tip_y + across[1] * spread),
            (tip_x - across[0] * spread, tip_y - across[1] * spread),
        ])

    def _login_width(self, form: LoginForm, headline: str, subhead: str = "",
                     hint: str = "", celebrate: bool = False,
                     replay: bool = False) -> int:
        """Wide enough for its own contents, never narrower than the design width.

        A long name or a returning player's address should push the panel out
        rather than run off the end of it.
        """
        pad = round(26 * self.scale)
        lines = [
            (self.font_huge if celebrate else self.font_clock).size(headline)[0],
            self.font_label.size(subhead)[0] if subhead else 0,
            (self.font_label.size(hint)[0]
             + (self._replay_icon_size() + round(9 * self.scale) if replay else 0))
            if hint else 0,
            self.font_body.size(form.error)[0] if form.error else 0,
        ]
        return max(round(LOGIN_WIDTH * self.scale), max(lines) + 2 * pad)

    @staticmethod
    def _caret_is_showing() -> bool:
        """Blinks, so an empty focused field still looks like it wants typing."""
        return pygame.time.get_ticks() % 1000 < 600

    def _chequered_strip(self, rect: pygame.Rect) -> None:
        """A racing flag along the edge of the panel."""
        square = rect.width / FLAG_SQUARES
        half = rect.height / 2
        for column in range(FLAG_SQUARES):
            for row in range(2):
                colour = CHEQUER_LIGHT if (column + row) % 2 else CHEQUER_DARK
                pygame.draw.rect(self.screen, colour, pygame.Rect(
                    round(rect.left + column * square), round(rect.top + row * half),
                    round(square) + 1, round(half) + 1))

    def _panel(self, rect: pygame.Rect) -> None:
        """A translucent slab, so text stays readable over grass or tarmac."""
        panel = pygame.Surface(rect.size, pygame.SRCALPHA, 32)
        pygame.draw.rect(panel, PANEL, panel.get_rect(), border_radius=round(10 * self.scale))
        self.screen.blit(panel, rect.topleft)


# --- placing shapes ---------------------------------------------------------


def _rotate(shape: list[tuple[float, float]], angle: float) -> list[tuple[float, float]]:
    cos, sin = math.cos(angle), math.sin(angle)
    return [(px * cos - py * sin, px * sin + py * cos) for px, py in shape]


def _offset(shape: list[tuple[float, float]], dx: float,
            dy: float) -> list[tuple[float, float]]:
    return [(px + dx, py + dy) for px, py in shape]


def _place(shape: list[tuple[float, float]], x: float, y: float,
           heading: float) -> list[tuple[float, float]]:
    """Rotate a local-space polygon by `heading` and drop it at `(x, y)`."""
    cos, sin = math.cos(heading), math.sin(heading)
    return [(x + px * cos - py * sin, y + px * sin + py * cos) for px, py in shape]
