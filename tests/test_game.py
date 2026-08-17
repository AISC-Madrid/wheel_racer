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
from wheel_racer.game import COUNTDOWN_SECONDS, RESULT_SECONDS, Game, State
from wheel_racer.inputs import KeyboardInput, WristSample
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


@pytest.fixture
def game(display) -> Game:
    pygame.event.clear()
    instance = Game(source=KeyboardInput(), screen=display, world=build_world())
    instance.source = WristAutopilot(instance)
    return instance


def run_for(game: Game, seconds: float, until: State | None = None) -> None:
    for _ in range(round(seconds / DT)):
        game.step(DT)
        if until is not None and game.state is until:
            return


def press_space() -> None:
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))


class TestArriving:
    def test_starts_on_the_attract_screen(self, game):
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
    def booth(self, display) -> Game:
        pygame.event.clear()
        instance = Game(source=KeyboardInput(), screen=display, world=build_world(),
                        auto_start=True)
        instance.source = WristAutopilot(instance)
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


class TestDrivingARun:
    @pytest.fixture
    def finished(self, game) -> Game:
        press_space()
        run_for(game, COUNTDOWN_SECONDS + 0.2)
        run_for(game, 120.0, until=State.RESULT)
        return game

    def test_the_run_reaches_the_result_screen(self, finished):
        assert finished.state is State.RESULT

    def test_it_records_one_split_per_lap(self, finished):
        assert len(finished.splits) == config.LAPS_PER_RUN

    def test_the_first_run_is_the_best_run(self, finished):
        assert finished.best_run == pytest.approx(sum(finished.splits))

    def test_the_total_is_in_the_right_ballpark(self, finished):
        assert 0.7 * config.TARGET_RUN_SECONDS < sum(finished.splits) < 1.4 * config.TARGET_RUN_SECONDS

    def test_the_standing_start_makes_the_first_lap_slower(self, finished):
        """Which is what makes lap one a recognition lap and lap two the quick
        one — a player always improves on their own first attempt."""
        assert finished.splits[0] > finished.splits[1]

    def test_the_result_screen_times_out_back_to_attract(self, finished):
        run_for(finished, RESULT_SECONDS + 0.5)
        assert finished.state is State.ATTRACT

    def test_a_second_run_can_be_started(self, finished):
        press_space()
        finished.step(DT)
        assert finished.state is State.COUNTDOWN
        assert finished.splits == []


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
