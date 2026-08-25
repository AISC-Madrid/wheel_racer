"""The second screen: a timing tower for the monitor facing the stand.

This is not the game's HUD made bigger. The two screens have opposite jobs and
therefore opposite rules. The laptop screen is read from thirty centimetres by
the one person holding the bar; this one is read from three to five metres by
somebody who has not decided to stop walking yet, and it has about a second to
change their mind.

Everything here follows from that:

  * **Eight rows, not thirty.** A full field in comfortable list type is a
    spreadsheet, and nobody stops for a spreadsheet. Eight rows means each one
    can be tall enough to read across a stand.
  * **The live half is the eye-catcher, the board is the payoff.** A ranking
    does not move, and stationary things do not catch eyes — but something
    changes here every thirty seconds, so the left column carries whatever is
    happening right now and the tower is what it resolves into.
  * **Deltas, not times.** `14.88` means nothing to a passer-by. `+0.67` means
    "they nearly had it", which is a story, and it is the reason a timing tower
    is worth building instead of a list.
  * **First names only.** The booth collects addresses; a screen pointed at a
    public stand is the last place any of them should appear.

It runs as its own process against `live.json` and `players.csv`, so it can be
started, killed and restarted without touching the game — and if it falls over
mid-afternoon, the person at the wheel never finds out.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import pygame

from . import config
from .display import is_fullscreen_shortcut, open_display, quit_on_signals
from .effects import Fireworks
from .live import LiveChannel, LiveState
from .players import Player, PlayerBook
from .render import format_time
from .trackart import CHEQUER_DARK, CHEQUER_LIGHT

# --- palette ----------------------------------------------------------------
# Darker and higher-contrast than the game's, because this screen is looked at
# from across an outdoor stand. Mid-greys and thin type disappear in daylight,
# so there are none: everything is either bright or background.

BACKDROP = (14, 16, 22)
BACKDROP_LIT = (22, 26, 36)
RULE = (44, 50, 64)
TEXT = (244, 246, 250)
TEXT_DIM = (150, 160, 178)
LIVE = (120, 190, 235)
AHEAD = (126, 214, 148)
BEHIND = (232, 116, 74)

# Instantly parseable without reading a word, which is most of the job.
PODIUM = ((255, 205, 92), (196, 202, 214), (198, 132, 78))

# --- layout, in design pixels on a 1920x1080 monitor ------------------------
DESIGN = (1920, 1080)
LIVE_COLUMN = 0.38
"""Share of the width given to the live half."""

ROWS = 8
MARGIN = 46.0
FLAG_HEIGHT = 26.0
FLAG_SQUARES = 24
ROW_GAP = 8.0

# How long a new leader owns the whole screen before the tower comes back. Long
# enough for a queue to look up, short enough that the next player is not kept
# waiting by it.
TAKEOVER_SECONDS = 6.0


@dataclass(frozen=True)
class Standing:
    """One row of the tower, ready to draw and carrying no personal data."""

    position: int
    name: str
    seconds: float
    delta: float | None
    """Gap to the leader, or None for the leader themselves."""
    is_driving: bool


def standings(players: list[Player], driver: str = "") -> list[Standing]:
    """Turn the player book into rows, quickest first.

    Names are taken as typed but shown as first names only — partly because it
    is all that fits at this size, and partly because "Marta G." on a public
    screen is more identifying than a booth needs to be.
    """
    rows = []
    leader = players[0].best_seconds if players else 0.0
    for index, player in enumerate(players):
        first = (player.name.split() or [""])[0]
        rows.append(Standing(
            position=index + 1,
            name=first.upper(),
            seconds=player.best_seconds,
            delta=None if index == 0 else player.best_seconds - leader,
            is_driving=bool(driver) and first.casefold() == driver.split()[0].casefold()
            if driver.split() else False,
        ))
    return rows


class LeaderboardScreen:
    """Draws the tower. Owns its fonts and its own copy of the fireworks."""

    def __init__(self, screen: pygame.Surface) -> None:
        self.screen = screen
        width, height = screen.get_size()
        # Same trick as the game: author at one size, scale to the monitor, so
        # this works on whatever is on the stand without a second layout.
        self.scale = min(width / DESIGN[0], height / DESIGN[1])
        self.fireworks = Fireworks()
        self._takeover_until = 0.0
        self._celebrated_for: str | None = None

        mono = "menlo,dejavusansmono,consolas,monospace"
        self.font_hero = pygame.font.SysFont(mono, self._pt(150), bold=True)
        self.font_clock = pygame.font.SysFont(mono, self._pt(112), bold=True)
        self.font_row = pygame.font.SysFont(mono, self._pt(52), bold=True)
        self.font_name = pygame.font.SysFont(mono, self._pt(64), bold=True)
        self.font_label = pygame.font.SysFont(mono, self._pt(26), bold=True)
        self.font_body = pygame.font.SysFont(mono, self._pt(34), bold=True)

    def _pt(self, design_points: int) -> int:
        return max(10, round(design_points * self.scale))

    def _px(self, design_pixels: float) -> int:
        return round(design_pixels * self.scale)

    # --- frame ---------------------------------------------------------------

    def draw(self, rows: list[Standing], live: LiveState | None,
             drivers: int, dt: float) -> None:
        """One frame: the tower, the live half, and any takeover over the top."""
        now = time.time()
        self._notice_new_leader(rows, live, now)

        self.screen.fill(BACKDROP)
        width, height = self.screen.get_size()
        split = round(width * LIVE_COLUMN)

        self._draw_live_column(pygame.Rect(0, 0, split, height), live, rows)
        self._draw_tower(pygame.Rect(split, 0, width - split, height), rows, drivers)
        pygame.draw.line(self.screen, RULE, (split, self._px(MARGIN)),
                         (split, height - self._px(MARGIN)), max(1, self._px(2)))

        # Order matters: the veil goes down first so the tower dims behind it,
        # the sparks go over the veil so they are not buried under it, and the
        # headline goes last so nothing is ever drawn across the time itself.
        celebrating = now < self._takeover_until
        if celebrating:
            self._draw_veil()
        self.fireworks.update(dt, (width, height), celebrating, self.scale)
        if len(self.fireworks):
            self._draw_fireworks()
        if celebrating:
            self._draw_takeover(rows)

    def _notice_new_leader(self, rows: list[Standing], live: LiveState | None,
                           now: float) -> None:
        """Fire the takeover once, when someone actually takes the top spot.

        Driven off the board rather than off the game's `beat_their_best`,
        because most personal bests are not a new *record* — celebrating every
        one of them here would spend the effect several times an hour and leave
        nothing for the moment that matters. Latched on the leader's name so a
        heartbeat re-publish cannot set it off twice.
        """
        leader = rows[0].name if rows else None
        if leader is None:
            return
        if self._celebrated_for is None:
            # First frame after starting up: adopt whoever is already top
            # rather than announcing a record that was set before we booted.
            self._celebrated_for = leader
            return
        if leader != self._celebrated_for and live is not None and not live.is_stale():
            self._celebrated_for = leader
            self._takeover_until = now + TAKEOVER_SECONDS

    # --- the live half -------------------------------------------------------

    def _draw_live_column(self, area: pygame.Rect, live: LiveState | None,
                          rows: list[Standing]) -> None:
        """Whatever is happening at the wheel, or the target if nobody is there."""
        if live is None or live.is_stale() or live.state in ("idle", "ready"):
            self._stack(area, self._target_lines(rows))
        elif live.state == "countdown":
            self._stack(area, [
                (self.font_label, "GET READY", TEXT_DIM, 0.0),
                (self.font_hero, self._first_name(live.name), LIVE, 0.4),
            ])
        else:
            self._stack(area, self._driver_lines(live, rows))

    def _target_lines(self, rows: list[Standing]) -> list[tuple]:
        """The number to beat, in the largest type on the stand.

        The most motivating thing that can be on this screen while nobody is
        playing: a challenge with a name attached, and what the queue is looking
        at while they decide whether to have a go.
        """
        if not rows:
            return [(self.font_label, "NOBODY HAS SET A TIME", TEXT_DIM, 0.0),
                    (self.font_hero, "BE FIRST", LIVE, 0.4)]
        return [
            (self.font_label, "TIME TO BEAT", TEXT_DIM, 0.0),
            (self.font_hero, format_time(rows[0].seconds), PODIUM[0], 0.3),
            (self.font_name, rows[0].name, TEXT, 0.4),
        ]

    def _driver_lines(self, live: LiveState, rows: list[Standing]) -> list[tuple]:
        """The lap in progress, and how it is going against the leader.

        The comparison is the point of the whole column. `12.84` on its own is
        a number a passer-by cannot place; what it is doing against the record
        is a story they can follow without being told the rules.

        Mid-lap, only *losing* the record is a fact. Being under the leader's
        finished time part-way round means nothing at all, so while the clock
        is still below it the line underneath stays the target — and the moment
        it goes past, that target turns into the gap. Which is exactly the beat
        the queue is watching for.
        """
        elapsed = live.elapsed()
        lines = [
            (self.font_label, "NOW DRIVING" if live.is_driving else "FINISHED",
             TEXT_DIM, 0.0),
            (self.font_name, self._first_name(live.name), TEXT, 0.3),
            (self.font_clock, format_time(elapsed),
             LIVE if live.is_driving else TEXT, 0.35),
        ]

        record = rows[0].seconds if rows else None
        if record is not None and elapsed is not None:
            gap = elapsed - record
            if live.is_driving and gap <= 0:
                lines.append((self.font_body, f"BEAT {format_time(record)}",
                              TEXT_DIM, 0.35))
            elif not live.is_driving and abs(gap) < 0.005:
                # They *are* the leader — their own time is the one being
                # compared against. "+0.00" in the losing colour would be a
                # strange thing to show the person who just took the record.
                lines.append((self.font_body, "TRACK RECORD", AHEAD, 0.35))
            else:
                lines.append((self.font_body, f"{'+' if gap >= 0 else ''}{gap:.2f}",
                              BEHIND if gap >= 0 else AHEAD, 0.35))

        if live.laps:
            lines.append((self.font_label, f"LAP {live.lap}/{live.laps}",
                          TEXT_DIM, 0.5))
        if live.personal_best is not None:
            lines.append((self.font_label,
                          f"THEIR BEST {format_time(live.personal_best)}",
                          TEXT_DIM, 0.3))
        return lines

    @staticmethod
    def _first_name(name: str) -> str:
        parts = name.upper().split()
        return parts[0] if parts else "DRIVER"

    def _stack(self, area: pygame.Rect, lines: list[tuple]) -> None:
        """Centre a column of lines as one block.

        Laid out as a block rather than pinned to the edges, because the number
        of lines changes with what is happening — a returning player has a
        personal best to show and a new one does not — and anything anchored
        top and bottom leaves a different hole every time it changes.

        Each line's gap is a fraction of its own height, so the spacing scales
        with the type rather than needing a second set of numbers.
        """
        rendered = []
        for font, text, colour, gap in lines:
            surface = font.render(text, True, colour)
            rendered.append((surface, round(gap * surface.get_height())))

        total = sum(surface.get_height() + gap for surface, gap in rendered)
        y = area.centery - total // 2
        for surface, gap in rendered:
            y += gap
            self.screen.blit(surface, (area.centerx - surface.get_width() // 2, y))
            y += surface.get_height()

    # --- the tower -----------------------------------------------------------

    def _draw_tower(self, area: pygame.Rect, rows: list[Standing],
                    drivers: int) -> None:
        inner = area.inflate(-2 * self._px(MARGIN), -2 * self._px(MARGIN))
        self._chequered_strip(pygame.Rect(inner.left, inner.top, inner.width,
                                          self._px(FLAG_HEIGHT)))

        footer = self.font_label.get_height() + self._px(20)
        top = inner.top + self._px(FLAG_HEIGHT + 24)
        available = inner.bottom - footer - top
        row_height = (available - self._px(ROW_GAP) * (ROWS - 1)) / ROWS

        for index in range(ROWS):
            spot = pygame.Rect(inner.left, round(top + index * (row_height
                               + self._px(ROW_GAP))), inner.width, round(row_height))
            self._draw_row(spot, rows[index] if index < len(rows) else None, index)

        tally = f"{drivers} DRIVER{'' if drivers == 1 else 'S'} TODAY"
        label = self.font_label.render(tally, True, TEXT_DIM)
        self.screen.blit(label, (inner.left, inner.bottom - label.get_height()))

    def _draw_row(self, spot: pygame.Rect, row: Standing | None, index: int) -> None:
        """One line of the tower. An empty slot is drawn, not skipped.

        Eight slots from the first player of the day onwards, because a board
        that grows a row at a time reads as a stand that is filling up, and a
        board that starts three rows tall reads as one nobody is playing.
        """
        pad = self._px(22)
        if row is not None and row.is_driving:
            pygame.draw.rect(self.screen, BACKDROP_LIT, spot,
                             border_radius=self._px(10))
        pygame.draw.line(self.screen, RULE, (spot.left, spot.bottom),
                         (spot.right, spot.bottom), max(1, self._px(1)))

        colour = PODIUM[index] if index < len(PODIUM) else TEXT
        position = self.font_row.render(str(index + 1), True,
                                        colour if row else RULE)
        self.screen.blit(position, (spot.left + pad,
                                    spot.centery - position.get_height() // 2))

        if row is None:
            return

        name = self._fit(row.name, self.font_name, spot.width * 0.52)
        self.screen.blit(name, (spot.left + pad + self._px(90),
                                spot.centery - name.get_height() // 2))

        # The leader carries the only absolute time on the board; everyone else
        # carries their gap to it, which is the number that means something.
        if row.delta is None:
            value = self.font_row.render(format_time(row.seconds), True, colour)
        else:
            value = self.font_row.render(f"+{row.delta:.2f}", True, TEXT_DIM)
        self.screen.blit(value, (spot.right - pad - value.get_width(),
                                 spot.centery - value.get_height() // 2))

    def _fit(self, text: str, font: pygame.font.Font,
             limit: float) -> pygame.Surface:
        """Render a name, shrinking it rather than cutting it off.

        Somebody will type a very long name into a form that has no reason to
        stop them, and an ellipsis on the second screen would be the one place
        at the stand where a player's name is visibly not good enough.
        """
        surface = font.render(text, True, TEXT)
        if surface.get_width() <= limit:
            return surface
        shrunk = pygame.transform.smoothscale_by(surface, limit / surface.get_width())
        return shrunk

    # --- the new-leader takeover ---------------------------------------------

    def _draw_takeover(self, rows: list[Standing]) -> None:
        """The whole screen, for the moment somebody takes the record.

        Worth interrupting the tower for, because it is the only thing that
        happens all afternoon that a stand full of people can share.
        """
        width, height = self.screen.get_size()
        area = self.screen.get_rect()
        self._chequered_strip(pygame.Rect(0, 0, width, self._px(FLAG_HEIGHT)))
        self._chequered_strip(pygame.Rect(0, height - self._px(FLAG_HEIGHT),
                                          width, self._px(FLAG_HEIGHT)))
        self._centred(area, self.font_body, "NEW TRACK RECORD", AHEAD,
                      offset=-self._px(240))
        if rows:
            self._centred(area, self.font_hero, format_time(rows[0].seconds),
                          PODIUM[0], offset=-self._px(80))
            self._centred(area, self.font_name, rows[0].name, TEXT,
                          offset=self._px(90))

    def _draw_veil(self) -> None:
        """Put the tower behind glass, so the record has the screen to itself.

        Not quite opaque: leaving the board faintly readable underneath is what
        makes this land as *the tower being interrupted* rather than as a
        different screen that happened to appear.
        """
        veil = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA, 32)
        veil.fill((*BACKDROP, 242))
        self.screen.blit(veil, (0, 0))

    def _draw_fireworks(self) -> None:
        """Reused wholesale from the game, which is why it takes a scale."""
        width, height = self.screen.get_size()
        layer = pygame.Surface((width, height), pygame.SRCALPHA, 32)
        self.fireworks.draw(layer, self.scale)
        self.screen.blit(layer, (0, 0), special_flags=pygame.BLEND_RGB_ADD)

    # --- pieces --------------------------------------------------------------

    def _centred(self, area: pygame.Rect, font: pygame.font.Font, text: str,
                 colour: tuple[int, int, int], offset: int = 0) -> None:
        surface = font.render(text, True, colour)
        self.screen.blit(surface, (area.centerx - surface.get_width() // 2,
                                   area.centery + offset - surface.get_height() // 2))

    def _chequered_strip(self, rect: pygame.Rect) -> None:
        """The same racing flag the game uses, at this screen's size."""
        square = rect.width / FLAG_SQUARES
        half = rect.height / 2
        for column in range(FLAG_SQUARES):
            for row in range(2):
                colour = CHEQUER_LIGHT if (column + row) % 2 else CHEQUER_DARK
                pygame.draw.rect(self.screen, colour, pygame.Rect(
                    round(rect.left + column * square), round(rect.top + row * half),
                    math.ceil(square) + 1, math.ceil(half) + 1))


def run(display: int = 1, size: tuple[int, int] | None = None,
        fullscreen: bool = False, book: PlayerBook | None = None,
        channel: LiveChannel | None = None) -> None:
    """Open the tower and keep it up until it is closed.

    Opens as a window on the second monitor rather than going straight to
    fullscreen, because setting a stand up means dragging windows onto the
    right screens and a fullscreen window is the one thing you cannot drag.
    Put it where it belongs, then press F11.
    """
    pygame.init()
    pygame.display.set_caption("Hand-Wheel Racer — Leaderboard")
    quit_on_signals()

    windowed_size = size or (1280, 720)
    screen = open_display(None if fullscreen else windowed_size, fullscreen, display)
    board = LeaderboardScreen(screen)
    players = book if book is not None else PlayerBook()
    live_channel = channel if channel is not None else LiveChannel()
    clock = pygame.time.Clock()

    running = True
    while running:
        dt = clock.tick(config.TARGET_FPS) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            elif is_fullscreen_shortcut(event):
                fullscreen = not fullscreen
                screen = open_display(None if fullscreen else windowed_size,
                                      fullscreen, display)
                board = LeaderboardScreen(screen)
            elif event.type == pygame.VIDEORESIZE and not fullscreen:
                screen = open_display(event.size, False, display)
                # Every size on this screen is worked out from the window, so a
                # resize means building the whole thing again — which is cheap,
                # and happens only while somebody is dragging a corner.
                windowed_size = screen.get_size()
                board = LeaderboardScreen(screen)

        state = live_channel.read()
        everyone = players.all()
        rows = standings(sorted(everyone, key=lambda p: p.best_seconds)[:ROWS],
                         driver=state.name if state else "")
        board.draw(rows, state, len(everyone), dt)
        pygame.display.flip()

    pygame.quit()
