"""The game loop and the states a booth visitor passes through.

Five states, and the shape of them is set by booth throughput rather than by
anything about racing: sign someone in, get them driving in a couple of seconds,
take about thirty seconds off them, show them a number, and be ready for the
next person without anyone having to touch the machine.

  LOGIN -> ATTRACT -> COUNTDOWN -> RACING -> RESULT -> ATTRACT or LOGIN

Signing in gates everything: there is no path into a run that skips the form,
because the names and addresses are what the booth is there to collect. RESULT
puts the same form back up with the player's time above it, so handing over is
the same few keystrokes as arriving — and ENTER on an untouched form means the
same person going again.

RESULT does not expire. A time left on screen is the booth advertising itself
to the queue, and a screen that resets on a timer either wipes a half-typed
address or clears somebody's result before they have shown their friends. It
stays until the next person does something about it.
"""

from __future__ import annotations

from enum import Enum, auto

import pygame

from . import config
from .car import Car
from .effects import DustCloud, Fireworks, TyreTrail
from .inputs import InputSource, PreviewFrame
from .laptimer import LapTimer
from .login import LoginForm
from .players import PlayerBook, normalise_email
from .recovery import RecoveryMonitor
from .render import Renderer, format_time
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
RESPAWN_FLASH_SECONDS = 1.2

# Smallest window we will build a circuit in. Nothing breaks above this; below
# it the fonts bottom out at their minimum size and the panels start to overlap,
# and at zero width the scale would be zero and the maths would divide by it.
MIN_WINDOW = (640, 360)

# The fullscreen shortcut, checked ahead of the sign-in form so it cannot be
# swallowed by whoever is typing. F11 is the convention, but macOS usually eats
# it before any application sees it, so Cmd-F and Ctrl-F work too — and they
# need the modifier precisely because a bare F belongs to whoever is spelling
# their name.
FULLSCREEN_MODIFIERS = pygame.KMOD_META | pygame.KMOD_CTRL


def _is_fullscreen_shortcut(event: pygame.event.Event) -> bool:
    if event.type != pygame.KEYDOWN:
        return False
    if event.key == pygame.K_F11:
        return True
    return event.key == pygame.K_f and bool(event.mod & FULLSCREEN_MODIFIERS)


class State(Enum):
    LOGIN = auto()
    ATTRACT = auto()
    COUNTDOWN = auto()
    RACING = auto()
    RESULT = auto()


class Game:
    """Owns the car, the clock and the state the booth is in."""

    def __init__(
        self,
        source: InputSource,
        screen: pygame.Surface,
        world: World,
        auto_start: bool = False,
        book: PlayerBook | None = None,
        fullscreen: bool = False,
    ) -> None:
        self.source = source
        self.world = world
        self.screen = screen
        self.renderer = Renderer(screen, world)
        self.fullscreen = fullscreen
        # The size to come back to when fullscreen is switched off again.
        self._windowed_size = screen.get_size() if not fullscreen else (
            config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        # Whether holding the bar level is enough to start a run. On the camera
        # it is the entire onboarding; on the keyboard it would fire the moment
        # nobody was pressing a key, so there it stays off and SPACE starts.
        self.auto_start = auto_start

        self.car = Car(*world.track.start_pose())
        self.steering = SteeringFilter(
            dead_zone_deg=config.STEER_DEAD_ZONE_DEG,
            max_angle_deg=config.STEER_MAX_ANGLE_DEG,
            smoothing_tau=config.STEER_SMOOTHING_TAU,
            invert=config.STEER_INVERT,
        )
        self.timer = LapTimer(
            gates=config.NUM_CHECKPOINT_GATES,
            max_progress_step=config.LAP_MAX_PROGRESS_STEP,
        )
        self.recovery = RecoveryMonitor(
            off_track_seconds=config.RESPAWN_AFTER_OFF_TRACK_S,
            backwards_seconds=config.RESPAWN_AFTER_BACKWARDS_S,
        )
        self.trail = TyreTrail()
        self.dust = DustCloud()
        self.fireworks = Fireworks()

        # Signing in is what the booth is actually collecting, so it gates
        # play: there is no way into a run that does not go through the form.
        self.book = book if book is not None else PlayerBook()
        self.form = LoginForm()
        self.player_name = ""
        self.player_email = ""
        self.personal_best: float | None = None
        self.beat_their_best = False

        self.state = State.LOGIN
        self.now = 0.0
        self.splits: list[float] = []
        self.best_run: float | None = None
        self.hands_lost_for = 0.0
        self.preview: PreviewFrame | None = None
        self.located: TrackPoint = world.track.locate(self.car.x, self.car.y)
        self._state_deadline = 0.0
        self._respawn_flash_until = 0.0
        self._level_for = 0.0

    @property
    def signed_in(self) -> bool:
        return bool(self.player_email)

    @property
    def hands_present(self) -> bool:
        """Whether the last poll produced a pair of wrists."""
        return self.hands_lost_for == 0.0

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
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False

            # Display keys are read before the form gets a look in, so the
            # window can be resized while somebody is halfway through signing in.
            if _is_fullscreen_shortcut(event):
                self.set_fullscreen(not self.fullscreen)
                continue
            if event.type == pygame.VIDEORESIZE and not self.fullscreen:
                self.adopt_display(open_display(event.size, fullscreen=False))
                continue

            if self.state in (State.LOGIN, State.RESULT):
                # Every other key belongs to the form while it is up, including
                # space — which is a character in somebody's name long before it
                # is a button.
                self._handle_form(event)
            elif (event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE
                    and self.state is State.ATTRACT):
                self._begin_countdown()
        return True

    def _handle_form(self, event: pygame.event.Event) -> None:
        """Typing, and the one shortcut that shares a key with it.

        On the result screen ENTER means two different things, told apart by
        whether anything has been typed: an untouched form means the same
        player going again, and a filled one means they are handing over. That
        keeps the whole screen to one key rather than teaching a queue two.
        """
        if (self.state is State.RESULT and self.form.is_empty
                and event.type == pygame.KEYDOWN
                and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER)):
            self._begin_countdown()
            return

        self.form.handle(event)
        if self.form.submitted:
            self._sign_in()

    def _sign_in(self) -> None:
        """Take the form's word for it and let them at the wheel.

        A returning address brings its best time back with it, so the HUD shows
        what they have to beat rather than starting them from nothing.
        """
        self.player_name = self.form.name
        self.player_email = normalise_email(self.form.email)
        self.personal_best = self.book.best_for(self.player_email)
        self.form.clear()
        self.splits = []
        self.state = State.ATTRACT
        self._reset_car()

    def _read_steering(self, dt: float) -> float:
        """One steering value per frame, whether or not a sample arrived."""
        self.preview = self.source.preview()
        sample = self.source.poll(dt)
        if sample is None:
            self.hands_lost_for += dt
            return self.steering.hold()
        self.hands_lost_for = 0.0
        return self.steering.update(sample.left, sample.right, dt)

    @property
    def celebrating(self) -> bool:
        """Whether the screen should be letting off fireworks."""
        return self.state is State.RESULT and self.beat_their_best

    def _advance(self, steering: float, dt: float) -> None:
        # Ahead of the state machine, and in every state, because a burst that
        # is already in the air has to finish falling wherever the player has
        # got to — including on the sign-in form they just started typing into.
        self.fireworks.update(dt, self.screen.get_size(), self.celebrating,
                              self.world.scale)

        if self.state is State.LOGIN:
            return
        if self.state is State.ATTRACT:
            self._advance_attract(steering, dt)
        elif self.state is State.COUNTDOWN and self.now >= self._state_deadline:
            self._begin_race()
        elif self.state is State.RACING:
            self._advance_race(steering, dt)

    def _advance_attract(self, steering: float, dt: float) -> None:
        """Start a run when someone picks the bar up and holds it level.

        This is the whole onboarding. A hesitant passer-by does not have to
        press anything, be told anything, or be watched failing to calibrate —
        they take hold of the prop and the game begins, which is what makes it
        read as an arcade cabinet rather than as a performance.
        """
        if not self.auto_start:
            return

        level = self.hands_present and abs(steering) <= config.ATTRACT_LEVEL_TOLERANCE
        self._level_for = self._level_for + dt if level else 0.0
        if self._level_for >= config.ATTRACT_HOLD_SECONDS:
            self._begin_countdown()

    def _advance_race(self, steering: float, dt: float) -> None:
        # One projection per frame, taken before the car moves and reused for
        # the surface, the lap progress and the recovery check. The frame of lag
        # this puts on lap timing is the same for everyone and far below what
        # anyone could perceive or exploit.
        self.located = self.world.track.locate(self.car.x, self.car.y)
        self.car.update(steering, self.located.on_track, dt, self.world.tuning)

        self.trail.update(self.car.x, self.car.y, dt)
        self.dust.update(self.car.x, self.car.y, self.car.heading, self.car.speed,
                         self.located.on_track, dt, self.world.scale)

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

        best_lap = min(self.splits)
        if self.best_run is None or best_lap < self.best_run:
            self.best_run = best_lap

        # Asked before the save, because "did they beat it" is exactly "did
        # the stored value move". Comparing the raw time against the saved one
        # instead would call a tie a personal best, since the file keeps
        # hundredths and the clock does not.
        previous = self.book.best_for(self.player_email)
        stored = self.book.record(self.player_name, self.player_email, best_lap)
        self.beat_their_best = previous is None or stored.best_seconds < previous
        self.personal_best = stored.best_seconds

        self.form.clear()
        self.state = State.RESULT

    def _respawn(self) -> None:
        """Put a lost car back on the racing line, facing the right way.

        It keeps grass speed rather than stopping, so the player rolls away
        immediately instead of waiting to be let go.
        """
        x, y, heading = self.located.pose
        self.car.reset(x, y, heading, speed=self.world.tuning.grass_speed)
        # Without this the trail draws a straight line from wherever the car was
        # stranded to where it reappears, which looks like it drove through the
        # infield — the exact thing the lap validator exists to forbid.
        self.trail.clear()
        self._respawn_flash_until = self.now + RESPAWN_FLASH_SECONDS

    def _abandon(self) -> None:
        self.state = State.ATTRACT
        self.splits = []
        self.hands_lost_for = 0.0
        self._reset_car()

    def _begin_countdown(self) -> None:
        self.state = State.COUNTDOWN
        self._state_deadline = self.now + COUNTDOWN_SECONDS
        # Whatever is left of the last player's celebration goes out here rather
        # than raining down over somebody else's first corner.
        self.fireworks.clear()
        self.splits = []
        self._level_for = 0.0
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
        # One player's line must not still be on the track behind the next
        # player's car.
        self.trail.clear()
        self.dust.clear()

    # --- display -------------------------------------------------------------

    def set_fullscreen(self, fullscreen: bool) -> None:
        """Switch between fullscreen and the last windowed size."""
        if fullscreen == self.fullscreen:
            return
        self.fullscreen = fullscreen
        self.adopt_display(
            open_display(None if fullscreen else self._windowed_size, fullscreen)
        )

    def adopt_display(self, screen: pygame.Surface) -> None:
        """Rebuild the circuit for a new window size, without losing the run.

        The circuit is authored in a fixed design space, so a resize is only a
        change of scale: everything is rebuilt at the new one and the car is put
        back on the same point of the same corner it was already on. Its speed
        is rescaled with it, because speeds are pixels per second and the pixels
        just changed size — leave it alone and the car briefly drives at the
        wrong pace for the track it is on.

        Lap timing is untouched. Progress is a fraction of the way round, which
        does not know how big the screen is, so a resize mid-run neither breaks
        the checkpoint validation nor gives anyone a faster lap.
        """
        design = self.world.to_design(self.car.x, self.car.y)
        old_scale = self.world.scale

        self.screen = screen
        if not self.fullscreen:
            # Remembered from the surface rather than from the drag, so that
            # leaving fullscreen comes back to the size actually in use — the
            # window has a minimum, and a drag below it does not get honoured.
            self._windowed_size = screen.get_size()
        self.world = build_world(*screen.get_size())
        self.renderer = Renderer(screen, self.world)

        x, y = self.world.from_design(*design)
        self.car.reset(x, y, self.car.heading,
                       self.car.speed * self.world.scale / old_scale)
        self.located = self.world.track.locate(self.car.x, self.car.y)
        # All three are stored in world pixels, so at the new scale they would
        # be drawn in the wrong places. There is nothing to rescale them from
        # that is worth the code — a resize is not something that happens
        # mid-corner, and the fireworks start again on the next frame anyway.
        self.trail.clear()
        self.dust.clear()
        self.fireworks.clear()

    # --- drawing -------------------------------------------------------------

    @property
    def lap_time(self) -> float | None:
        """What the big clock shows.

        The lap in progress while driving, and the best lap of the run once it
        is over — because the best lap is the score. A running total would be
        the wrong number in the largest text on screen, and next to a personal
        best measured in laps it would invite a comparison that means nothing.
        """
        running = self.timer.current_time(self.now)
        if running is not None:
            return running
        return min(self.splits) if self.splits else None

    def _draw(self, steering: float) -> None:
        self.renderer.draw_world(self.car, steering, self.trail, self.dust)
        self.renderer.draw_fireworks(self.fireworks)
        self.renderer.draw_preview(self.preview)

        if not self.source.is_healthy:
            # Not the player's problem to solve, so say so plainly rather than
            # leaving a queue trying harder at a camera that has gone away.
            self.renderer.draw_centre_message(
                "CAMERA LOST", "check the webcam is plugged in"
            )
            return

        if self.state is State.LOGIN:
            self.renderer.draw_login(
                self.form,
                headline="HAND WHEEL RACER",
                subhead=f"sign in to play  ·  {config.LAPS_PER_RUN} laps",
                hint="TAB to move on  ·  ENTER to take the wheel",
            )
            return

        if self.state is State.ATTRACT:
            self.renderer.draw_centre_message(
                f"READY, {self.player_name.upper()}" if self.player_name else "READY",
                self._attract_prompt(),
            )
            return

        self.renderer.draw_hud(
            run_time=self.lap_time,
            lap=min(len(self.splits) + 1, config.LAPS_PER_RUN),
            laps_total=config.LAPS_PER_RUN,
            best=self.personal_best,
            steering=steering,
            on_track=self.located.on_track or self.state is not State.RACING,
        )

        if self.state is State.COUNTDOWN:
            self.renderer.draw_centre_message(self._countdown_text(), huge=True)
        elif self.state is State.RESULT:
            self.renderer.draw_login(
                self.form,
                headline=format_time(min(self.splits)),
                subhead=self._splits_line(),
                hint=self._result_hint(),
                celebrate=self.beat_their_best,
                replay=self.form.is_empty,
            )
        elif self.now < self._respawn_flash_until:
            self.renderer.draw_centre_message("BACK ON TRACK")

    def _splits_line(self) -> str:
        """Every lap, so the headline figure can be seen where it came from."""
        laps = "  ·  ".join(f"lap {i} {format_time(s)}"
                            for i, s in enumerate(self.splits, start=1))
        return f"NEW BEST LAP  ·  {laps}" if self.beat_their_best else f"BEST LAP  ·  {laps}"

    def _result_hint(self) -> str:
        """What ENTER will do, which depends on whether anything is typed."""
        if not self.form.is_empty:
            return "ENTER to sign in and take over"
        return "ENTER to replay  ·  or type to sign in"

    def _attract_prompt(self) -> str:
        """One line, telling the player the next thing to do and nothing else."""
        if not self.auto_start:
            # Naming the mode on screen, because a keyboard-mode window looks
            # almost exactly like a camera-mode one that has stopped tracking.
            return f"keyboard mode  ·  space to start  ·  {config.LAPS_PER_RUN} laps"
        if not self.hands_present:
            return "take the wheel with both hands"
        if self._level_for > 0.0:
            return "hold it there..."
        return "hold the wheel level to start"

    def _countdown_text(self) -> str:
        remaining = self._state_deadline - self.now
        return str(int(remaining) + 1) if remaining > 0 else "GO"


def open_display(size: tuple[int, int] | None, fullscreen: bool) -> pygame.Surface:
    """Open, or reopen, the game window.

    Fullscreen deliberately asks for ``(0, 0)``, which SDL reads as "whatever
    the desktop is already at". Naming a size instead puts the display through
    a mode change to something smaller and stretches it back up, which on a
    booth laptop is a soft, faintly blurry picture. The game scales itself to
    any window, so it would rather have the real pixels.

    Windowed mode is resizable, because the booth screen is not known in
    advance and dragging a corner is a faster way to find the size that suits
    a stand than restarting with different numbers.
    """
    if fullscreen:
        return pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    width, height = size or (config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
    return pygame.display.set_mode(
        (max(width, MIN_WINDOW[0]), max(height, MIN_WINDOW[1])), pygame.RESIZABLE
    )


def start(
    source: InputSource,
    width: int,
    height: int,
    fullscreen: bool = False,
    auto_start: bool = False,
) -> None:
    """Open a window and play until the player quits."""
    pygame.init()
    pygame.display.set_caption("Hand-Wheel Racer — AISC Madrid")

    # Held keys repeat, so backspacing a mistyped address is one press rather
    # than forty.
    pygame.key.set_repeat(400, 40)

    screen = open_display((width, height), fullscreen)

    try:
        Game(source, screen, build_world(*screen.get_size()),
             auto_start=auto_start, fullscreen=fullscreen).run()
    finally:
        source.close()
        pygame.quit()
