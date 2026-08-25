"""The things on screen that move but are not the car.

The trail and the dust exist for the same reason: at 185 pixels a second across
a still picture, the car looks slower than it is and the grass penalty looks
like nothing at all. A trail behind the car and dust thrown up off the tarmac
give the eye something to measure motion against, and turn "my time got worse"
into something you can actually see happening.

The trail earns its place twice over. The one skill this game asks for is
drawing a clean line, and a trail is that line, drawn.

The fireworks are there for the queue rather than for the player. A booth game
needs a moment that carries across a stand — something that makes the person
two places back look up and see that whoever is at the wheel just did well. A
number turning green does not do that from three metres away; a screen full of
sparks does.

All three hold their own state and age it by dt, so none depends on the
framerate, and all three scale with the display. None knows anything about
pygame beyond drawing itself.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass

import pygame

TRAIL_COLOUR = (28, 29, 34)
DUST_COLOUR = (150, 176, 132)

# Long enough to show the shape of a corner you have just taken, short enough
# that a whole lap of it does not turn the circuit into a scribble.
TRAIL_SECONDS = 1.6
TRAIL_MAX_POINTS = 220
TRAIL_MIN_STEP = 4.0
TRAIL_WIDTH = 7.0
TRAIL_ALPHA = 90

DUST_PER_SECOND = 70.0
DUST_SECONDS = 0.65
DUST_SPEED = 55.0
DUST_SPREAD = 0.9
DUST_SIZE = 3.4
DUST_MAX = 220

# --- fireworks --------------------------------------------------------------
# Borrowed from things already on screen — the car's red, the checkpoint blue,
# the club logo's pink — so the celebration looks like it belongs to this game
# rather than like a screensaver that wandered in. Gold and white are there
# because a burst with no pale colour in it reads as murky over dark grass.
FIREWORK_COLOURS = (
    (255, 214, 102),   # gold
    (255, 255, 236),   # white
    (236, 112, 88),    # the car
    (120, 190, 235),   # the checkpoint blue
    (232, 92, 168),    # the club pink
    (150, 240, 176),   # mint
)

# Gap between one shell going up and the next. A shell takes the better part of
# a second to climb, so this has to be a good deal shorter than that or the sky
# holds one lonely burst at a time — which reads as a glitch, not a party.
FIREWORK_INTERVAL = (0.16, 0.44)
FIREWORK_RISE_SPEED = (470.0, 640.0)
# Where a shell bursts, as a fraction of the screen height. The floor matters
# as much as the ceiling: a burst is a couple of hundred pixels across, so one
# aimed much nearer the top than this loses its whole upper half off the screen
# and comes out looking like a semicircle.
FIREWORK_APEX = (0.17, 0.58)
FIREWORK_LAUNCH_BAND = (0.08, 0.92)
"""Fraction of the width a shell can be launched across."""

FIREWORK_SPARKS = (34, 58)
FIREWORK_BURST_SPEED = (150.0, 430.0)
FIREWORK_SPARK_LIFE = (0.9, 2.0)
FIREWORK_GRAVITY = 190.0
# Air resistance, as the fraction of its speed a spark keeps each second. This
# is what gives a burst its shape: sparks sprint outwards, then hang and fall,
# instead of flying off the screen in straight lines. Lower is draggier, and
# too low collapses the burst into a ball before it has finished opening.
FIREWORK_DRAG = 0.42
FIREWORK_SPARK_SIZE = 4.0
FIREWORK_ROCKET_SIZE = 2.6
# How far back a spark is smeared, as the distance it covers in this long. A
# firework is only ever seen as streaks — the eye holds the light for a moment
# after it has moved — and drawing round dots instead is what makes a particle
# system look like falling confetti rather than like fire.
FIREWORK_STREAK_SECONDS = 0.075
# A ceiling rather than a target. Six or seven bursts can be in the air at once
# and each is up to 52 sparks, so this is really only insurance against the
# result screen being left up all afternoon.
FIREWORK_MAX_SPARKS = 900


@dataclass
class _Mark:
    x: float
    y: float
    age: float = 0.0


@dataclass
class _Grain:
    x: float
    y: float
    vx: float
    vy: float
    age: float = 0.0


@dataclass
class _Rocket:
    """A shell on its way up, before it bursts."""

    x: float
    y: float
    vy: float
    apex: float
    colour: tuple[int, int, int]


@dataclass
class _Spark:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    colour: tuple[int, int, int]
    size: float
    age: float = 0.0

    @property
    def fade(self) -> float:
        return max(0.0, 1.0 - self.age / self.life)


class TyreTrail:
    """A fading line showing where the car has just been."""

    def __init__(self, seconds: float = TRAIL_SECONDS,
                 max_points: int = TRAIL_MAX_POINTS,
                 min_step: float = TRAIL_MIN_STEP) -> None:
        self.seconds = seconds
        self.min_step = min_step
        self._marks: deque[_Mark] = deque(maxlen=max_points)

    def __len__(self) -> int:
        return len(self._marks)

    def clear(self) -> None:
        """Wipe the trail. Called between runs, so one player's line does not
        appear behind the next player's car."""
        self._marks.clear()

    def update(self, x: float, y: float, dt: float) -> None:
        """Age the existing trail and, if the car has moved far enough, extend it.

        Spacing by distance rather than by frame keeps the trail the same shape
        whatever the framerate, and stops a stationary car burning a hole in it.
        """
        for mark in self._marks:
            mark.age += dt
        while self._marks and self._marks[0].age > self.seconds:
            self._marks.popleft()

        if not self._marks or math.dist((x, y), (self._marks[-1].x,
                                                 self._marks[-1].y)) >= self.min_step:
            self._marks.append(_Mark(x, y))

    def draw(self, surface: pygame.Surface, scale: float = 1.0) -> None:
        """Draw onto a transparent surface, oldest and faintest first."""
        if len(self._marks) < 2:
            return
        width = max(1, round(TRAIL_WIDTH * scale))
        marks = list(self._marks)
        for before, after in zip(marks, marks[1:]):
            fade = max(0.0, 1.0 - before.age / self.seconds)
            colour = (*TRAIL_COLOUR, int(TRAIL_ALPHA * fade))
            pygame.draw.line(surface, colour, (before.x, before.y),
                             (after.x, after.y), width)


class DustCloud:
    """Grass and dirt kicked up while the car is off the tarmac."""

    def __init__(self, seconds: float = DUST_SECONDS, per_second: float = DUST_PER_SECOND,
                 maximum: int = DUST_MAX, seed: int | None = None) -> None:
        self.seconds = seconds
        self.per_second = per_second
        self.maximum = maximum
        self._random = random.Random(seed)
        self._grains: list[_Grain] = []
        self._pending = 0.0

    def __len__(self) -> int:
        return len(self._grains)

    def clear(self) -> None:
        self._grains.clear()
        self._pending = 0.0

    def update(self, x: float, y: float, heading: float, speed: float,
               on_track: bool, dt: float, scale: float = 1.0) -> None:
        """Drift and fade what is in the air, and throw up more if off-track."""
        for grain in self._grains:
            grain.age += dt
            grain.x += grain.vx * dt
            grain.y += grain.vy * dt
        self._grains = [g for g in self._grains if g.age < self.seconds]

        if on_track or speed <= 0.0:
            self._pending = 0.0
            return
        self._emit(x, y, heading, speed, dt, scale)

    def _emit(self, x: float, y: float, heading: float, speed: float,
              dt: float, scale: float) -> None:
        """Spawn grains behind the car, thrown backwards and outwards.

        Rate is per second rather than per frame, with the fraction carried
        over, so the plume looks the same at 60fps and at 20.
        """
        self._pending += self.per_second * dt
        wanted = int(self._pending)
        self._pending -= wanted

        backwards = heading + math.pi
        for _ in range(wanted):
            if len(self._grains) >= self.maximum:
                return
            angle = backwards + self._random.uniform(-DUST_SPREAD, DUST_SPREAD)
            velocity = DUST_SPEED * scale * self._random.uniform(0.4, 1.3)
            self._grains.append(_Grain(
                x=x + math.cos(backwards) * 8.0 * scale,
                y=y + math.sin(backwards) * 8.0 * scale,
                vx=math.cos(angle) * velocity,
                vy=math.sin(angle) * velocity,
            ))

    def draw(self, surface: pygame.Surface, scale: float = 1.0) -> None:
        for grain in self._grains:
            fade = max(0.0, 1.0 - grain.age / self.seconds)
            radius = max(1, round(DUST_SIZE * scale * fade))
            pygame.draw.circle(surface, (*DUST_COLOUR, int(210 * fade)),
                               (int(grain.x), int(grain.y)), radius)


class Fireworks:
    """Shells going up and bursting, for a lap worth making a noise about.

    Two stages, because the rise is what makes a burst land. A firework that
    simply appears is a puff of colour; one you watched climb for half a second
    is an event, and the difference costs one extra particle per shell.

    Nothing here knows why it is celebrating. `update` is handed a `launching`
    flag every frame, exactly as the dust is handed `on_track`: when it goes
    false the sky stops filling but everything already up there still rises,
    bursts and falls. That is what stops the screen blinking clean the instant
    a player touches a key, which would look like a bug rather than an ending.
    """

    def __init__(self, maximum: int = FIREWORK_MAX_SPARKS,
                 seed: int | None = None) -> None:
        self.maximum = maximum
        self._random = random.Random(seed)
        self._rockets: list[_Rocket] = []
        self._sparks: list[_Spark] = []
        self._next_launch = 0.0

    def __len__(self) -> int:
        return len(self._rockets) + len(self._sparks)

    @property
    def bursting(self) -> bool:
        """Whether there is anything alight, however faint."""
        return bool(self._sparks)

    def clear(self) -> None:
        """Wipe the sky. One player's celebration must not still be going off
        over the next player's countdown."""
        self._rockets.clear()
        self._sparks.clear()
        self._next_launch = 0.0

    def update(self, dt: float, size: tuple[int, int], launching: bool,
               scale: float = 1.0) -> None:
        """Fly everything on by dt, and send up another shell if it is time."""
        self._advance_sparks(dt, scale)
        self._advance_rockets(dt, scale)

        if not launching:
            # Zeroed rather than left to run down, so the next celebration puts
            # a shell up on its very first frame. The result screen appears the
            # instant the lap is validated, and a random half-second of empty
            # sky before anything happens reads as the game having missed it.
            self._next_launch = 0.0
            return

        self._next_launch -= dt
        if self._next_launch <= 0.0:
            self._launch(size, scale)
            self._next_launch = self._random.uniform(*FIREWORK_INTERVAL)

    def _advance_sparks(self, dt: float, scale: float) -> None:
        """Drag, then gravity — in that order, which is what shapes a burst.

        Drag damps whatever the spark is already doing; gravity is added
        afterwards and is not damped. The two settle against each other at a
        terminal velocity of about `gravity * dt / (1 - keep)`, so a spark
        thrown upwards is stopped and turned over, and one already plunging is
        held back rather than accelerating off the bottom of the screen. That
        ceiling is the difference between a burst that droops and hangs and one
        that rains straight down like a broken pipe.

        Raising drag to the power of dt, rather than multiplying by it, is what
        keeps all of this identical at 30fps and at 60.
        """
        keep = FIREWORK_DRAG ** dt
        for spark in self._sparks:
            spark.age += dt
            spark.vx *= keep
            spark.vy = spark.vy * keep + FIREWORK_GRAVITY * scale * dt
            spark.x += spark.vx * dt
            spark.y += spark.vy * dt
        self._sparks = [s for s in self._sparks if s.age < s.life]

    def _advance_rockets(self, dt: float, scale: float) -> None:
        risen = []
        for rocket in self._rockets:
            rocket.vy += FIREWORK_GRAVITY * scale * dt
            rocket.y += rocket.vy * dt
            # Burst at the height it was aimed at, or at the top of its climb if
            # it ran out of speed first — a shell that stalls low still has to
            # go off, or it would fall back down as a dud.
            if rocket.y <= rocket.apex or rocket.vy >= 0.0:
                self._burst(rocket, scale)
            else:
                risen.append(rocket)
        self._rockets = risen

    def _launch(self, size: tuple[int, int], scale: float) -> None:
        width, height = size
        low, high = FIREWORK_LAUNCH_BAND
        self._rockets.append(_Rocket(
            x=self._random.uniform(width * low, width * high),
            y=float(height),
            vy=-self._random.uniform(*FIREWORK_RISE_SPEED) * scale,
            apex=height * self._random.uniform(*FIREWORK_APEX),
            colour=self._random.choice(FIREWORK_COLOURS),
        ))

    def _burst(self, rocket: _Rocket, scale: float) -> None:
        """Scatter a shell into sparks, on a ring rather than at random angles.

        Evenly spaced angles with a jitter, instead of a uniform draw, because
        random angles clump: a burst comes out lopsided and gappy about as often
        as it comes out round, and the round ones are the whole point.
        """
        count = self._random.randint(*FIREWORK_SPARKS)
        step = 2.0 * math.pi / count
        # Roughly a fifth of shells are two-tone, which is enough for the
        # occasional one to look special without the palette turning to soup.
        second = (self._random.choice(FIREWORK_COLOURS)
                  if self._random.random() < 0.2 else rocket.colour)

        for index in range(count):
            if len(self._sparks) >= self.maximum:
                return
            angle = index * step + self._random.uniform(-step / 2.0, step / 2.0)
            # Square-rooted so speeds bunch towards the outside of the burst,
            # giving a shell with an edge rather than an even smear.
            speed = (FIREWORK_BURST_SPEED[0] + (FIREWORK_BURST_SPEED[1]
                     - FIREWORK_BURST_SPEED[0]) * math.sqrt(self._random.random()))
            speed *= scale
            self._sparks.append(_Spark(
                x=rocket.x, y=rocket.y,
                vx=math.cos(angle) * speed,
                vy=math.sin(angle) * speed + rocket.vy * 0.25,
                life=self._random.uniform(*FIREWORK_SPARK_LIFE),
                colour=rocket.colour if index % 2 == 0 else second,
                size=FIREWORK_SPARK_SIZE * self._random.uniform(0.7, 1.3),
            ))

    def draw(self, surface: pygame.Surface, scale: float = 1.0) -> None:
        """Draw onto a black layer, to be added to the picture rather than laid
        over it.

        Fading is carried in the colour, not in the alpha channel, because the
        caller blits this additively and additive blending ignores alpha. Doing
        it this way is what makes light *pool*: two sparks crossing come out
        brighter than either, a dense burst blows out towards white at its
        centre, and a spark going out dims through its own hue instead of
        turning grey. Compositing normally gives none of that, and over dark
        grass the whole thing reads as dust.

        Three marks per spark — halo, streak, core — and they go down in that
        order across the whole burst rather than spark by spark. The layer is
        added to the picture, but the *drawing onto it* still replaces pixels
        the way pygame always does, so a neighbouring spark's dim halo laid
        down afterwards would rub a hole in a bright core. Sorting the passes
        dimmest-first means the brightest mark on any pixel is the one that
        survives, which is the same thing addition would have given.
        """
        # Faint on purpose. The halos of a fresh burst all overlap, and at any
        # real opacity they add up into one solid disc with the sparks lost
        # somewhere inside it.
        for spark in self._sparks:
            pygame.draw.circle(surface, _dim(spark.colour, spark.fade * 0.2),
                               (int(spark.x), int(spark.y)),
                               max(2, round(spark.size * scale * 2.0)))

        for spark in self._sparks:
            fade = spark.fade
            back = (spark.x - spark.vx * FIREWORK_STREAK_SECONDS,
                    spark.y - spark.vy * FIREWORK_STREAK_SECONDS)
            pygame.draw.line(surface, _dim(spark.colour, fade * 0.55),
                             (spark.x, spark.y), back,
                             max(1, round(spark.size * scale * (0.4 + 0.6 * fade))))

        for spark in self._sparks:
            fade = spark.fade
            # Whites out at the head while the spark is young, which is what
            # gives a fresh burst its hot centre.
            pygame.draw.circle(surface, _dim(spark.colour, min(1.0, fade * 1.7)),
                               (int(spark.x), int(spark.y)),
                               max(1, round(spark.size * scale * (0.4 + 0.6 * fade))))

        for rocket in self._rockets:
            radius = max(1, round(FIREWORK_ROCKET_SIZE * scale))
            # A longer tail than a spark gets: the climb is the part that has to
            # be followed across a room, and it is only ever one particle wide.
            tail = (rocket.x, rocket.y - rocket.vy * 0.09)
            pygame.draw.line(surface, _dim(rocket.colour, 0.35),
                             (rocket.x, rocket.y), tail, radius)
            pygame.draw.circle(surface, _dim(rocket.colour, 0.9),
                               (int(rocket.x), int(rocket.y)), radius)


def _dim(colour: tuple[int, int, int], amount: float) -> tuple[int, int, int, int]:
    """A colour scaled towards black, ready to be added to the picture.

    The alpha is a formality — additive blending never reads it — but pygame
    wants four components on a surface that has them.
    """
    amount = max(0.0, min(1.0, amount))
    return (int(colour[0] * amount), int(colour[1] * amount),
            int(colour[2] * amount), 255)
