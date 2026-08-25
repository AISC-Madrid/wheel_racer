"""The state machine, driven end to end without a display.

A player's whole visit, frame by frame: arrive, start, drive two laps, see a
time. Everything is wired here rather than in isolation, so this is what catches
a steering sign that got flipped in the wiring, or a lap that completes but
never reaches the result screen.

Steering is supplied as fake wrist points rather than as a steering value, so
the dead zone, the saturation and the smoothing are all exercised on the way in
— the same path the camera will take.
"""

import math

import pygame
import pytest

from wheel_racer import config
from wheel_racer.display import is_fullscreen_shortcut
from wheel_racer.game import COUNTDOWN_SECONDS, Game, State
from wheel_racer.inputs import KeyboardInput, WristSample
from wheel_racer.players import PlayerBook
from wheel_racer.world import build_world

DT = 1.0 / 60.0
TAU = 2.0 * math.pi
LOOKAHEAD_PX = 90.0
STEERING_GAIN = 2.5


class WristAutopilot:
    """An input source that drives, by moving a pair of imaginary wrists.

    It works out the steering it wants, then poses the bar at whatever angle
    produces it — the inverse of what `SteeringFilter` does — so the game is
    steered exactly the way a player steers it.
    """

    def __init__(self, game: Game) -> None:
        self.game = game
        self.hands_off = False
        self.camera_broken = False

    def poll(self, dt: float) -> WristSample | None:
        if self.hands_off:
            return None

        track, car = self.game.world.track, self.game.car
        here = track.locate(car.x, car.y)
        target_x, target_y, _ = track.pose_at(
            here.progress + LOOKAHEAD_PX / track.total_length
        )
        wanted = math.atan2(target_y - car.y, target_x - car.x)
        error = (wanted - car.heading + math.pi) % TAU - math.pi
        return self._bar_at(max(-1.0, min(1.0, error * STEERING_GAIN)))

    def preview(self):
        return None

    @property
    def is_healthy(self) -> bool:
        return not self.camera_broken

    def close(self) -> None:
        pass

    @staticmethod
    def _bar_at(steering: float) -> WristSample:
        span = config.STEER_MAX_ANGLE_DEG - config.STEER_DEAD_ZONE_DEG
        degrees = math.copysign(
            config.STEER_DEAD_ZONE_DEG + abs(steering) * span, steering
        )
        dx = 100.0 * math.cos(math.radians(degrees))
        dy = 100.0 * math.sin(math.radians(degrees))
        return WristSample(left=(320 - dx, 240 - dy), right=(320 + dx, 240 + dy))


@pytest.fixture(scope="module")
def display():
    pygame.init()
    surface = pygame.display.set_mode((config.WINDOW_WIDTH, config.WINDOW_HEIGHT))
    yield surface
    pygame.quit()


def build_game(display, tmp_path, **kwargs) -> Game:
    """A game writing to a throwaway CSV, never the booth's real one."""
    pygame.event.clear()
    instance = Game(source=KeyboardInput(), screen=display, world=build_world(),
                    book=PlayerBook(tmp_path / "players.csv"), **kwargs)
    instance.source = WristAutopilot(instance)
    return instance


@pytest.fixture
def unsigned(display, tmp_path) -> Game:
    """Freshly opened, waiting for someone to sign in."""
    return build_game(display, tmp_path)


@pytest.fixture
def game(unsigned) -> Game:
    """Signed in and ready to drive, which is where most tests start."""
    sign_in(unsigned)
    return unsigned


NAME, EMAIL = "Lauren", "lauren@example.com"


def press(key: int = 0, unicode: str = "") -> None:
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key, unicode=unicode))


def type_text(text: str) -> None:
    for character in text:
        press(unicode=character)


def sign_in(game: Game, name: str = NAME, email: str = EMAIL) -> None:
    """Fill the form the way a player would, then hand it in."""
    type_text(name)
    press(pygame.K_TAB)
    type_text(email)
    press(pygame.K_RETURN)
    game.step(DT)


def run_for(game: Game, seconds: float, until: State | None = None) -> None:
    for _ in range(round(seconds / DT)):
        game.step(DT)
        if until is not None and game.state is until:
            return


def press_space() -> None:
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))


class TestSigningIn:
    """Nobody drives without leaving a name and an address — that is what the
    booth is there to collect."""

    def test_opens_on_the_sign_in_form(self, unsigned):
        run_for(unsigned, 0.5)
        assert unsigned.state is State.LOGIN

    def test_a_completed_form_lets_them_at_the_wheel(self, unsigned):
        sign_in(unsigned)
        assert unsigned.state is State.ATTRACT
        assert unsigned.signed_in

    def test_space_does_not_skip_the_form(self, unsigned):
        """Space is a character in somebody's name long before it is a button."""
        press(pygame.K_SPACE, " ")
        run_for(unsigned, 0.2)
        assert unsigned.state is State.LOGIN

    def test_a_bad_address_is_refused_with_a_reason(self, unsigned):
        sign_in(unsigned, email="not-an-email")
        assert unsigned.state is State.LOGIN
        assert unsigned.form.error

    def test_a_missing_name_is_refused(self, unsigned):
        sign_in(unsigned, name="")
        assert unsigned.state is State.LOGIN
        assert unsigned.form.error

    def test_a_returning_player_brings_their_best_time_back(self, unsigned):
        unsigned.book.record("Lauren", EMAIL, 28.5)
        sign_in(unsigned)
        assert unsigned.personal_best == pytest.approx(28.5)

    def test_a_new_player_has_nothing_to_beat_yet(self, unsigned):
        sign_in(unsigned)
        assert unsigned.personal_best is None


class TestArriving:
    def test_a_signed_in_player_waits_at_the_grid(self, game):
        run_for(game, 0.5)
        assert game.state is State.ATTRACT

    def test_the_car_waits_on_the_grid(self, game):
        run_for(game, 2.0)
        assert (game.car.x, game.car.y) == pytest.approx(
            game.world.track.start_pose()[:2]
        )

    def test_space_begins_the_countdown(self, game):
        press_space()
        game.step(DT)
        assert game.state is State.COUNTDOWN

    def test_the_car_does_not_move_during_the_countdown(self, game):
        press_space()
        run_for(game, COUNTDOWN_SECONDS - 0.2)
        assert game.car.speed == 0.0

    def test_the_countdown_hands_over_to_racing(self, game):
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 0.2)
        assert game.state is State.RACING


class TestStartingByHoldingTheWheel:
    """The camera-mode onboarding: no button, no calibration, no instructions
    beyond picking the bar up. Everything here is off in keyboard mode, where
    "level" would just mean nobody is pressing a key."""

    @pytest.fixture
    def booth(self, display, tmp_path) -> Game:
        instance = build_game(display, tmp_path, auto_start=True)
        sign_in(instance)
        return instance

    def test_holding_the_bar_level_starts_a_run(self, booth):
        run_for(booth, config.ATTRACT_HOLD_SECONDS + 0.3)
        assert booth.state is State.COUNTDOWN

    def test_it_takes_a_moment_rather_than_firing_instantly(self, booth):
        """A bar being handed between two people passes through level."""
        run_for(booth, config.ATTRACT_HOLD_SECONDS - 0.4)
        assert booth.state is State.ATTRACT

    def test_no_hands_means_no_start(self, booth):
        booth.source.hands_off = True
        run_for(booth, config.ATTRACT_HOLD_SECONDS + 2.0)
        assert booth.state is State.ATTRACT

    def test_letting_go_resets_the_hold(self, booth):
        run_for(booth, config.ATTRACT_HOLD_SECONDS - 0.4)
        booth.source.hands_off = True
        run_for(booth, 0.5)
        booth.source.hands_off = False
        run_for(booth, 0.3)
        assert booth.state is State.ATTRACT

    def test_keyboard_mode_does_not_auto_start(self, game):
        """Nobody pressing an arrow key reads as a perfectly level bar."""
        run_for(game, config.ATTRACT_HOLD_SECONDS + 2.0)
        assert game.state is State.ATTRACT


@pytest.fixture
def finished(game) -> Game:
    """A whole run driven, sitting on the result screen."""
    press_space()
    run_for(game, COUNTDOWN_SECONDS + 0.2)
    run_for(game, 120.0, until=State.RESULT)
    return game


class TestDrivingARun:
    def test_the_run_reaches_the_result_screen(self, finished):
        assert finished.state is State.RESULT

    def test_it_records_one_split_per_lap(self, finished):
        assert len(finished.splits) == config.LAPS_PER_RUN

    def test_the_first_run_sets_the_mark(self, finished):
        assert finished.best_run == pytest.approx(min(finished.splits))

    def test_the_total_is_in_the_right_ballpark(self, finished):
        assert 0.7 * config.TARGET_RUN_SECONDS < sum(finished.splits) < 1.4 * config.TARGET_RUN_SECONDS

    def test_the_standing_start_makes_the_first_lap_slower(self, finished):
        """Which is what makes lap one a recognition lap and lap two the quick
        one — a player always improves on their own first attempt."""
        assert finished.splits[0] > finished.splits[1]

    def test_the_result_stays_up_indefinitely(self, finished):
        """A time left on screen is the booth advertising itself. Nothing about
        a clock should take it down before somebody has shown their friends."""
        run_for(finished, 90.0)
        assert finished.state is State.RESULT
        assert finished.signed_in

    def test_enter_on_an_untouched_form_means_race_again(self, finished):
        press(pygame.K_RETURN)
        finished.step(DT)
        assert finished.state is State.COUNTDOWN
        assert finished.splits == []
        assert finished.player_email == EMAIL

    def test_typing_on_the_result_screen_hands_over_instead(self, finished):
        sign_in(finished, name="Ada", email="ada@example.com")
        assert finished.state is State.ATTRACT
        assert finished.player_email == "ada@example.com"

    def test_the_run_is_written_to_the_csv(self, finished):
        """The saved figure is the best single lap of the run, not the total —
        see `_on_lap_complete`. Stored to the hundredth, which is the
        resolution the clock shows."""
        stored = finished.book.get(EMAIL)
        assert stored is not None
        assert stored.best_seconds == pytest.approx(min(finished.splits), abs=0.005)
        assert stored.name == NAME

    def test_a_first_run_always_counts_as_a_best(self, finished):
        assert finished.beat_their_best

    def test_the_headline_figure_is_the_best_lap(self, finished):
        """The score, and the number the CSV keeps. A run total in the largest
        text on screen would invite comparison with a personal best that is
        measured in laps."""
        assert finished.lap_time == pytest.approx(min(finished.splits))

    def test_the_clock_shows_the_lap_in_progress_while_driving(self, game):
        """Not a running total: with the best lap as the score, the total is
        not a number anybody is racing against."""
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 0.2)
        run_for(game, 120.0, until=State.RACING)
        run_for(game, 20.0)                       # into the second lap
        assert len(game.splits) == 1
        assert game.lap_time is not None
        assert game.lap_time < sum(game.splits) + game.lap_time

    def test_a_slower_second_run_is_not_a_new_best(self, game):
        """A tie must not read as an improvement either: the file keeps
        hundredths and the clock does not, so comparing the raw time against
        the saved one would call the same lap an improvement on itself."""
        game.book.record(NAME, EMAIL, 1.0)  # unbeatable
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 0.2)
        run_for(game, 120.0, until=State.RESULT)
        assert not game.beat_their_best
        assert game.personal_best == pytest.approx(1.0)

    def test_a_half_typed_address_is_never_wiped(self, finished):
        type_text("Ada")
        finished.step(DT)
        run_for(finished, 60.0)
        assert finished.state is State.RESULT
        assert finished.form.name == "Ada"

    def test_the_replay_prompt_is_only_offered_on_an_untouched_form(self, finished):
        """Once somebody starts typing, ENTER means sign in, so offering a
        replay symbol beside it would be pointing at the wrong action."""
        assert "replay" in finished._result_hint()
        type_text("A")
        finished.step(DT)
        assert "replay" not in finished._result_hint()

    def test_the_prompt_does_not_name_the_last_player(self, finished):
        """The screen is facing a queue, not the person who just got up."""
        assert NAME not in finished._result_hint()


class TestWalkingAway:
    def test_losing_both_hands_abandons_the_lap(self, game):
        """Otherwise the car circles on the last held steering value while a
        queue waits for the machine to free itself up."""
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 3.0)
        assert game.state is State.RACING

        game.source.hands_off = True
        run_for(game, config.ABANDON_AFTER_HANDS_LOST_S + 0.5)
        assert game.state is State.ATTRACT

    def test_a_brief_dropout_does_not_abandon(self, game):
        """Hands leave the bar for a moment all the time, especially outdoors."""
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 2.0)
        game.source.hands_off = True
        run_for(game, 1.0)
        assert game.state is State.RACING

    def test_the_car_keeps_steering_through_a_dropout(self, game):
        """Snapping to centre would yank the car off the road."""
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 4.0)
        before = game.car.heading

        game.source.hands_off = True
        game.step(DT)
        assert game.car.heading != before  # still turning, on the held value

    def test_abandoning_puts_the_car_back_on_the_grid(self, game):
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 3.0)
        game.source.hands_off = True
        run_for(game, config.ABANDON_AFTER_HANDS_LOST_S + 0.5)
        assert (game.car.x, game.car.y) == pytest.approx(
            game.world.track.start_pose()[:2]
        )


class TestBrokenCamera:
    """A camera that has stopped must not look like a player doing nothing."""

    def test_the_game_keeps_running_with_a_dead_camera(self, game):
        game.source.camera_broken = True
        game.source.hands_off = True
        run_for(game, 2.0)
        assert game.step(DT) is True

    def test_a_dead_camera_does_not_start_a_run(self, game):
        game.source.camera_broken = True
        run_for(game, config.ATTRACT_HOLD_SECONDS + 2.0)
        assert game.state is State.ATTRACT

    def test_recovering_the_camera_gets_back_to_normal(self, game):
        game.source.camera_broken = True
        run_for(game, 1.0)
        game.source.camera_broken = False
        press_space()
        game.step(DT)
        assert game.state is State.COUNTDOWN


class TestRescue:
    def test_a_car_stranded_on_the_grass_is_put_back(self, game):
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 1.0)

        # Strand it in the infield, pointing at nothing useful.
        game.car.reset(640.0, 360.0, heading=0.0, speed=game.world.tuning.grass_speed)
        run_for(game, config.RESPAWN_AFTER_OFF_TRACK_S + 0.5)

        assert game.world.track.locate(game.car.x, game.car.y).on_track

    def test_the_rescued_car_is_moving(self, game):
        """Dropping it back at a standstill would feel like a second penalty."""
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 1.0)
        game.car.reset(640.0, 360.0, heading=0.0, speed=0.0)
        run_for(game, config.RESPAWN_AFTER_OFF_TRACK_S + 0.5)
        assert game.car.speed > 0.0


class TestChangingTheWindow:
    """The booth screen is not known in advance, so the window has to be free to
    change size — including mid-run, if somebody goes fullscreen while a player
    is driving. The circuit is authored in a fixed design space and scaled to
    the window, so a resize must move the picture and nothing else.
    """

    @staticmethod
    def _bigger(size=(1920, 1080)) -> pygame.Surface:
        """A larger surface, without touching the shared display mode."""
        return pygame.Surface(size)

    def test_the_car_stays_where_it_was_on_the_circuit(self, game):
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 4.0)
        before = game.world.track.locate(game.car.x, game.car.y).progress

        game.adopt_display(self._bigger())

        after = game.world.track.locate(game.car.x, game.car.y).progress
        assert after == pytest.approx(before, abs=1e-3)

    def test_a_bigger_window_rescales_the_car(self, game):
        """Speeds are pixels per second and the pixels just changed size."""
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 4.0)
        share_of_top = game.car.speed / game.world.tuning.top_speed

        game.adopt_display(self._bigger())

        assert game.world.scale > 1.0
        assert game.car.speed / game.world.tuning.top_speed == pytest.approx(share_of_top)

    def test_the_track_still_fits_the_window(self, game):
        game.adopt_display(self._bigger())
        points = game.world.track.points
        assert points[:, 0].min() >= 0.0 and points[:, 0].max() <= 1920.0
        assert points[:, 1].min() >= 0.0 and points[:, 1].max() <= 1080.0

    def test_a_resize_mid_run_still_finishes_the_run(self, game):
        """Lap progress is a fraction of the way round, so it does not care how
        big the screen is — the checkpoint validation must survive the change."""
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 3.0)
        game.adopt_display(self._bigger())

        run_for(game, 120.0, until=State.RESULT)
        assert game.state is State.RESULT
        assert len(game.splits) == config.LAPS_PER_RUN

    def test_a_letter_f_is_typed_rather_than_going_fullscreen(self, unsigned):
        """The shortcut is read before the form, so it must not be a bare key."""
        type_text("Fernando")
        unsigned.step(DT)
        assert unsigned.form.fields[0].value == "Fernando"

    def test_the_modified_shortcut_is_recognised(self):
        plain = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_f, unicode="f", mod=0)
        held = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_f, unicode="f",
                                  mod=pygame.KMOD_LMETA)
        function_key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, mod=0)

        assert not is_fullscreen_shortcut(plain)
        assert is_fullscreen_shortcut(held)
        assert is_fullscreen_shortcut(function_key)


class TestCelebrating:
    """A personal best has to be visible from the back of the queue, which is
    the one thing a number turning green cannot do."""

    def test_a_personal_best_sets_them_off(self, finished):
        assert finished.beat_their_best
        run_for(finished, 2.0)
        assert len(finished.fireworks)

    def test_an_ordinary_result_does_not(self, finished):
        """Spent on every result, the effect marks nothing."""
        finished.beat_their_best = False
        run_for(finished, 3.0)
        assert not len(finished.fireworks)

    def test_they_stop_when_the_next_player_takes_the_wheel(self, finished):
        run_for(finished, 2.0)
        press(pygame.K_RETURN)
        finished.step(DT)
        assert finished.state is State.COUNTDOWN
        assert not len(finished.fireworks)

    def test_typing_does_not_blank_the_sky(self, finished):
        """The form and the celebration share a screen. Sparks vanishing the
        instant somebody starts typing would read as a crash, so what is in the
        air finishes falling."""
        run_for(finished, 2.0)
        type_text("Marta")
        finished.step(DT)
        assert finished.state is State.RESULT
        assert finished.fireworks.bursting

    def test_they_burn_out_rather_than_running_all_afternoon(self, finished):
        """RESULT never expires — a time left on screen is the booth
        advertising itself — so the fireworks have to be the part that stops."""
        run_for(finished, 2.0)
        finished.beat_their_best = False
        run_for(finished, 8.0)
        assert not len(finished.fireworks)
