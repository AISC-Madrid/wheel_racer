"""The game loop and the states a booth visitor passes through.

Four states, and the shape of them is set by booth throughput rather than by
anything about racing: get someone driving in a couple of seconds, take about
thirty seconds off them, show them a number, and be ready for the next person
without anyone having to touch the machine.

Registration and the leaderboard slot in later, between ATTRACT and COUNTDOWN
and after RESULT respectively; the states are laid out to leave room for them.
"""

from __future__ import annotations

from enum import Enum, auto

import pygame

from . import config
from .car import Car
from .inputs import InputSource
from .laptimer import LapTimer
from .recovery import RecoveryMonitor
from .render import Renderer
from .steering import SteeringFilter
from .track import TrackPoint
from .world import World, build_world

# Longest frame we will simulate in one step. A window drag, a garbage collect
# or a laggy first frame would otherwise advance the car by a huge distance —
# which looks like teleporting, and the lap validator would rightly reject it.
# Clamping makes the game briefly run in slow motion instead, which nobody
# notices and nothing breaks over.
MAX_FRAME_SECONDS = 0.05

COUNTDOWN_SECONDS = 3.0
RESULT_SECONDS = 8.0
RESPAWN_FLASH_SECONDS = 1.2


class State(Enum):
    ATTRACT = auto()
    COUNTDOWN = auto()
    RACING = auto()
    RESULT = auto()


class Game:
    """Owns the car, the clock and the state the booth is in."""

    def __init__(self, source: InputSource, screen: pygame.Surface, world: World) -> None:
        self.source = source
        self.world = world
        self.renderer = Renderer(screen, world)

        self.car = Car(*world.track.start_pose())
        self.steering = SteeringFilter(
            dead_zone_deg=config.STEER_DEAD_ZONE_DEG,
            max_angle_deg=config.STEER_MAX_ANGLE_DEG,
            smoothing_tau=config.STEER_SMOOTHING_TAU,
            invert=config.STEER_INVERT,
        )
        self.timer = LapTimer(
            num_checkpoints=config.NUM_CHECKPOINTS,
            max_progress_step=config.LAP_MAX_PROGRESS_STEP,
        )
        self.recovery = RecoveryMonitor(
            off_track_seconds=config.RESPAWN_AFTER_OFF_TRACK_S,
            backwards_seconds=config.RESPAWN_AFTER_BACKWARDS_S,
        )

        self.state = State.ATTRACT
        self.now = 0.0
        self.splits: list[float] = []
        self.best_run: float | None = None
        self.hands_lost_for = 0.0
        self.located: TrackPoint = world.track.locate(self.car.x, self.car.y)
        self._state_deadline = 0.0
        self._respawn_flash_until = 0.0

    # --- loop ----------------------------------------------------------------

    def run(self) -> None:
        clock = pygame.time.Clock()
        while self.step(min(clock.tick(config.TARGET_FPS) / 1000.0, MAX_FRAME_SECONDS)):
            pass

    def step(self, dt: float) -> bool:
        """One frame. Returns whether the game should keep running.

        Separate from `run` so tests can drive the state machine a frame at a
        time without a real clock.
        """
        self.now += dt
        running = self._handle_events()
        steering = self._read_steering(dt)
        self._advance(steering, dt)
        self._draw(steering)
        pygame.display.flip()
        return running

    def _handle_events(self) -> bool:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type != pygame.KEYDOWN:
                continue
            if event.key == pygame.K_ESCAPE:
                return False
            if event.key == pygame.K_SPACE:
                self._on_space()
        return True

    def _on_space(self) -> None:
        if self.state in (State.ATTRACT, State.RESULT):
            self._begin_countdown()

    def _read_steering(self, dt: float) -> float:
        """One steering value per frame, whether or not a sample arrived."""
        sample = self.source.poll(dt)
        if sample is None:
            self.hands_lost_for += dt
            return self.steering.hold()
        self.hands_lost_for = 0.0
        return self.steering.update(sample.left, sample.right, dt)

    def _advance(self, steering: float, dt: float) -> None:
        if self.state is State.COUNTDOWN and self.now >= self._state_deadline:
            self._begin_race()
        elif self.state is State.RACING:
            self._advance_race(steering, dt)
        elif self.state is State.RESULT and self.now >= self._state_deadline:
            self.state = State.ATTRACT

    def _advance_race(self, steering: float, dt: float) -> None:
        # One projection per frame, taken before the car moves and reused for
        # the surface, the lap progress and the recovery check. The frame of lag
        # this puts on lap timing is the same for everyone and far below what
        # anyone could perceive or exploit.
        self.located = self.world.track.locate(self.car.x, self.car.y)
        self.car.update(steering, self.located.on_track, dt, self.world.tuning)

        if self.recovery.update(self.located.on_track, self.car.heading,
                                self.located.heading, dt):
            self._respawn()

        lap = self.timer.update(self.located.progress, self.now)
        if lap is not None:
            self._on_lap_complete(lap)

        if self.hands_lost_for >= config.ABANDON_AFTER_HANDS_LOST_S:
            # Somebody walked off mid-lap. Without this the car would circle on
            # the last held steering value until the next person prised the bar
            # out of the air.
            self._abandon()

    def _on_lap_complete(self, lap: float) -> None:
        self.splits.append(lap)
        if len(self.splits) < config.LAPS_PER_RUN:
            self.timer.start(self.now)
            return

        total = sum(self.splits)
        if self.best_run is None or total < self.best_run:
            self.best_run = total
        self.state = State.RESULT
        self._state_deadline = self.now + RESULT_SECONDS

    def _respawn(self) -> None:
        """Put a lost car back on the racing line, facing the right way.

        It keeps grass speed rather than stopping, so the player rolls away
        immediately instead of waiting to be let go.
        """
        x, y, heading = self.located.pose
        self.car.reset(x, y, heading, speed=self.world.tuning.grass_speed)
        self._respawn_flash_until = self.now + RESPAWN_FLASH_SECONDS

    def _abandon(self) -> None:
        self.state = State.ATTRACT
        self.splits = []
        self.hands_lost_for = 0.0
        self._reset_car()

    def _begin_countdown(self) -> None:
        self.state = State.COUNTDOWN
        self._state_deadline = self.now + COUNTDOWN_SECONDS
        self.splits = []
        self.steering.reset()
        self.recovery.reset()
        self._reset_car()

    def _begin_race(self) -> None:
        self.state = State.RACING
        self.timer.start(self.now)
        self.recovery.reset()

    def _reset_car(self) -> None:
        self.car.reset(*self.world.track.start_pose())
        self.located = self.world.track.locate(self.car.x, self.car.y)

    # --- drawing -------------------------------------------------------------

    @property
    def run_time(self) -> float | None:
        """Elapsed time across the whole attempt, laps so far plus this one."""
        running = self.timer.current_time(self.now)
        if running is None and not self.splits:
            return None
        return sum(self.splits) + (running or 0.0)

    def _draw(self, steering: float) -> None:
        self.renderer.draw_world(self.car)

        if self.state is State.ATTRACT:
            self.renderer.draw_centre_message(
                "HAND WHEEL RACER",
                f"hold the bar level  ·  space to start  ·  {config.LAPS_PER_RUN} laps",
            )
            return

        self.renderer.draw_hud(
            run_time=self.run_time,
            lap=min(len(self.splits) + 1, config.LAPS_PER_RUN),
            laps_total=config.LAPS_PER_RUN,
            best=self.best_run,
            steering=steering,
            on_track=self.located.on_track or self.state is not State.RACING,
        )

        if self.state is State.COUNTDOWN:
            self.renderer.draw_centre_message(self._countdown_text(), huge=True)
        elif self.state is State.RESULT:
            self.renderer.draw_result(
                self.splits, self.best_run, is_best=sum(self.splits) == self.best_run
            )
        elif self.now < self._respawn_flash_until:
            self.renderer.draw_centre_message("BACK ON TRACK")

    def _countdown_text(self) -> str:
        remaining = self._state_deadline - self.now
        return str(int(remaining) + 1) if remaining > 0 else "GO"


def start(source: InputSource, width: int, height: int, fullscreen: bool = False) -> None:
    """Open a window and play until the player quits."""
    pygame.init()
    pygame.display.set_caption("Hand-Wheel Racer — AISC Madrid")

    flags = pygame.FULLSCREEN if fullscreen else 0
    screen = pygame.display.set_mode((width, height), flags)
    size = screen.get_size()

    try:
        Game(source, screen, build_world(*size)).run()
    finally:
        source.close()
        pygame.quit()
