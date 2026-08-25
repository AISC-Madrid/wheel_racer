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

import hashlib
import math
import time
from dataclasses import dataclass, field

import pygame

from . import config
from .display import is_fullscreen_shortcut, open_display, quit_on_signals
from .effects import Fireworks
from .backdrop import Backdrop, blend, lap_pace
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

# How quickly a row slides to a new position, as the time constant of an
# exponential ease. Slow enough to be followed by eye — the whole point is that
# somebody *saw* the overtake — and quick enough to be finished before the next
# player has finished reading their own time.
SLIDE_TAU = 0.16

# How long a row stays lit after climbing into the top three. This is the only
# thing on the board that marks the difference between a good lap and a lap
# that mattered, so it outlasts the slide that caused it.
PODIUM_FLASH_SECONDS = 2.4
PODIUM_PLACES = 3


@dataclass
class _RowMotion:
    """Where a row is being drawn, as opposed to where it belongs.

    `slot` is a fractional position in the tower, eased towards `target` — so
    two rows swapping are genuinely between places for a moment rather than
    jumping past each other.
    """

    slot: float
    target: int
    flash: float = 0.0

    def advance(self, dt: float) -> None:
        # Exponential ease rather than a fixed step per frame, so the slide
        # takes the same time at 60fps as at 30 — which matters here, because
        # this screen is the one that gets throttled when the laptop is busy.
        self.slot += (self.target - self.slot) * (1.0 - math.exp(-dt / SLIDE_TAU))
        if abs(self.target - self.slot) < 0.001:
            self.slot = float(self.target)
        self.flash = max(0.0, self.flash - dt / PODIUM_FLASH_SECONDS)


@dataclass(frozen=True)
class Standing:
    """One row of the tower, ready to draw and carrying no personal data."""

    position: int
    name: str
    seconds: float
    delta: float | None
    """Gap to the leader, or None for the leader themselves."""
    is_driving: bool
    key: str = ""
    """Which row this is, for the animation to follow between positions.

    A short digest of the address rather than the name, because two people
    called Marta at a student fair is not a hypothetical — and keyed by name,
    the second one arriving would inherit the first one's row and the tower
    would animate nonsense. Never drawn and never written anywhere: it exists
    only to tell one row from another between frames.
    """


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
            key=hashlib.blake2s(player.email.encode("utf-8"),
                                digest_size=8).hexdigest(),
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
        self.backdrop = Backdrop((width, height), BACKDROP)
        self._takeover_until = 0.0
        self._celebrated_for: str | None = None
        self._motion: dict[str, _RowMotion] = {}
        self._settled = False
        """Whether the first frame has been drawn.

        Rows animate *into* their positions, which is right for an overtake and
        wrong for the board simply appearing — without this, opening the screen
        would play eight simultaneous promotions to an empty stand.
        """

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
        self._advance_rows(rows, dt)

        # The circuit is the background — it is a prebuilt surface, so this is
        # the fill the screen was doing anyway rather than work on top of it.
        self.backdrop.update(dt, lap_pace(rows[0].seconds if rows else None))
        self.backdrop.draw(self.screen)

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

    def _advance_rows(self, rows: list[Standing], dt: float) -> None:
        """Move every row towards where it now belongs, and light the climbers.

        The tower's *positions* stay put and the drivers move between them,
        which is both how a real timing tower reads and considerably simpler:
        the numbers down the left never animate, so nothing has to be drawn
        over anything else while two rows are passing.
        """
        targets = {row.key: index for index, row in enumerate(rows)}

        for key, target in targets.items():
            motion = self._motion.get(key)
            if motion is None:
                # New arrivals rise from below the last slot rather than fading
                # in on the spot — the board should look like it is being
                # climbed into, not like rows are appearing out of the air.
                start = float(ROWS) if self._settled else float(target)
                self._motion[key] = _RowMotion(slot=start, target=target)
                if self._settled and target < PODIUM_PLACES:
                    self._motion[key].flash = 1.0
                continue

            climbed_onto_the_podium = (target < PODIUM_PLACES
                                       <= motion.target)
            motion.target = target
            if climbed_onto_the_podium:
                motion.flash = 1.0

        # Anyone pushed off the bottom stops being drawn and stops being
        # remembered; over an afternoon the alternative is a dictionary with
        # every player who has ever played in it.
        for key in [k for k in self._motion if k not in targets]:
            del self._motion[key]

        for motion in self._motion.values():
            motion.advance(dt)
        self._settled = True

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

        def slot_at(position: float) -> pygame.Rect:
            return pygame.Rect(
                inner.left,
                round(top + position * (row_height + self._px(ROW_GAP))),
                inner.width, round(row_height))

        # Three passes, bottom to top. Everything that paints a background goes
        # down before anything that paints a letter, so a row lit up mid-slide
        # cannot cover the position number it is sliding towards — which is
        # exactly what it did when each row drew itself in one go.
        moving = [(row, self._motion.get(row.key)) for row in rows]
        moving.sort(key=lambda pair: (pair[1].flash if pair[1] else 0.0))

        for index in range(ROWS):
            self._draw_slot_rule(slot_at(index))
        for row, motion in moving:
            self._draw_row_background(
                slot_at(motion.slot if motion else row.position - 1), row, motion)
        for index in range(ROWS):
            self._draw_slot_number(slot_at(index), index, occupied=index < len(rows))
        for row, motion in moving:
            self._draw_row_content(
                slot_at(motion.slot if motion else row.position - 1), row, motion)

        tally = f"{drivers} DRIVER{'' if drivers == 1 else 'S'} TODAY"
        label = self.font_label.render(tally, True, TEXT_DIM)
        self.screen.blit(label, (inner.left, inner.bottom - label.get_height()))

    def _draw_slot_rule(self, spot: pygame.Rect) -> None:
        """The line under a place, drawn whether or not anybody is in it.

        Eight slots from the first player of the day onwards, because a board
        that grows a row at a time reads as a stand that is filling up, and a
        board that starts three rows tall reads as one nobody is playing.
        """
        pygame.draw.line(self.screen, RULE, (spot.left, spot.bottom),
                         (spot.right, spot.bottom), max(1, self._px(1)))

    def _draw_slot_number(self, spot: pygame.Rect, index: int,
                          occupied: bool) -> None:
        """The position, which belongs to the slot and never moves.

        This is what a real timing tower does, and it is also what makes the
        animation cheap: only names and times slide, so nothing is ever drawn
        across a number while two rows are passing each other.
        """
        if not occupied:
            colour = RULE
        elif index < len(PODIUM):
            colour = PODIUM[index]
        else:
            colour = TEXT
        number = self.font_row.render(str(index + 1), True, colour)
        self.screen.blit(number, (spot.left + self._px(22),
                                  spot.centery - number.get_height() // 2))

    def _draw_row_background(self, spot: pygame.Rect, row: Standing,
                             motion: _RowMotion | None) -> None:
        flash = motion.flash if motion else 0.0
        if flash > 0.0:
            # Lit from the left edge, fading across — a full panel of gold at
            # this size is a slab, and this reads as the row being picked out.
            self._draw_flash(spot, flash)
        elif row.is_driving:
            pygame.draw.rect(self.screen, BACKDROP_LIT, spot,
                             border_radius=self._px(10))

    def _draw_row_content(self, spot: pygame.Rect, row: Standing,
                          motion: _RowMotion | None) -> None:
        """One driver's name and time, at whatever position they are between."""
        pad = self._px(22)
        # Coloured by where they are *now*, so the gold arrives as the row
        # arrives rather than a moment before or after it.
        place = int(round(motion.slot)) if motion else row.position - 1
        colour = PODIUM[place] if place < len(PODIUM) else TEXT

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

    def _draw_flash(self, spot: pygame.Rect, flash: float) -> None:
        """The glow on a row that has just climbed into the top three.

        Drawn as a handful of opaque bars shading back to the background rather
        than as an alpha surface, for the same reason the backdrop's trail is:
        this screen is sharing a laptop with the hand tracking, and a
        full-width translucent blit every frame is a cost worth not paying for
        a two-second highlight.
        """
        # Enough bands that the steps stop being visible as steps. Thirty-two
        # opaque rectangles is still less work than one of the row's two text
        # renders, so there is no reason to be stingy and see the banding.
        steps = 32
        for index in range(steps):
            fade = flash * (1.0 - index / steps) ** 2
            band = pygame.Rect(spot.left + spot.width * index // steps,
                               spot.top, spot.width // steps + 2, spot.height)
            pygame.draw.rect(self.screen, blend(BACKDROP_LIT, PODIUM[0], fade),
                             band)
        pygame.draw.rect(self.screen, blend(BACKDROP_LIT, PODIUM[0], flash),
                         pygame.Rect(spot.left, spot.top, self._px(6), spot.height))

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


class BoardSource:
    """The player book, read only when it has actually changed.

    The loop needs the standings every frame, and asking `PlayerBook` for them
    means opening and parsing the CSV — sixty times a second, for a file that
    changes once every thirty seconds when somebody finishes a run. Left alone
    that was most of what this process was doing, and it was doing it on the
    same laptop as the hand tracking.

    Cached on the file's timestamp, the way the live channel is. A file that
    has not been written is not re-read; one that has, is.
    """

    def __init__(self, book: PlayerBook) -> None:
        self.book = book
        self._stamp: float | None = None
        self._players: list[Player] = []

    def players(self) -> list[Player]:
        try:
            stamp = self.book.path.stat().st_mtime
        except OSError:
            # No file yet — the first minutes of the fair, before anyone has
            # finished a run. An empty board is a state this screen draws.
            self._stamp, self._players = None, []
            return self._players

        if stamp != self._stamp:
            self._stamp = stamp
            self._players = sorted(self.book.all(), key=lambda p: p.best_seconds)
        return self._players


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
    players = BoardSource(book if book is not None else PlayerBook())
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
        everyone = players.players()
        rows = standings(everyone[:ROWS], driver=state.name if state else "")
        board.draw(rows, state, len(everyone), dt)
        pygame.display.flip()

    pygame.quit()
