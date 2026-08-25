"""Tests for the circuit behind the leaderboard.

It exists to move, so the things worth pinning are that it does move, that it
moves at the pace it was asked to whatever the framerate, and that it stays
cheap — the whole justification for a decoration on a laptop that is also doing
hand tracking is that it costs almost nothing.
"""

import pygame
import pytest

from wheel_racer.backdrop import (DEFAULT_LAP_SECONDS, SLOWEST_LAP_SECONDS,
                                  Backdrop, blend, circuit_bounds, lap_pace)

SCREEN = (1920, 1080)
DT = 1.0 / 60.0


@pytest.fixture(scope="module")
def display():
    pygame.init()
    pygame.display.set_mode((320, 240))
    yield
    pygame.quit()


@pytest.fixture
def backdrop(display) -> Backdrop:
    return Backdrop(SCREEN, (14, 16, 22))


def lap(backdrop: Backdrop, seconds: float, pace: float) -> None:
    for _ in range(round(seconds / DT)):
        backdrop.update(DT, pace)


def distance_round(progress: float, target: float) -> float:
    """How far apart two points on the lap are, the short way round.

    Progress is a position on a loop, so plain subtraction says a car one
    thousandth short of the line is a whole lap from it.
    """
    gap = abs(progress - target) % 1.0
    return min(gap, 1.0 - gap)


class TestTheCar:
    def test_it_goes_round(self, backdrop):
        lap(backdrop, 2.0, 15.0)
        assert backdrop.progress > 0.0

    def test_it_takes_the_lap_time_it_was_given(self, backdrop):
        """The pace is the point — it is lapping at the record, so it has to
        actually take that long."""
        lap(backdrop, 15.0, 15.0)
        assert distance_round(backdrop.progress, 0.0) < 0.02

        lap(backdrop, 7.5, 15.0)
        assert distance_round(backdrop.progress, 0.5) < 0.02

    def test_it_wraps_rather_than_running_off_the_end(self, backdrop):
        lap(backdrop, 300.0, 12.0)
        assert 0.0 <= backdrop.progress < 1.0

    def test_it_laps_at_the_same_pace_at_any_framerate(self, display):
        """This is the screen that gets throttled when the laptop is busy, so
        a car that went round faster at 60fps than at 30 would be a car that
        changed speed whenever the game got demanding."""
        fast, slow = Backdrop(SCREEN, (0, 0, 0)), Backdrop(SCREEN, (0, 0, 0))
        for _ in range(600):
            fast.update(1 / 120.0, 10.0)
        for _ in range(150):
            slow.update(1 / 30.0, 10.0)
        assert distance_round(fast.progress, slow.progress) < 0.01

    def test_it_stays_on_the_track(self, backdrop):
        """It is placed by arc length along the centreline, so it cannot leave
        the road — which is what makes running the real physics here pointless.
        """
        for _ in range(400):
            backdrop.update(DT, 12.0)
            x, y, _ = backdrop.world.track.pose_at(backdrop.progress)
            assert backdrop.world.track.locate(x, y).on_track


class TestPace:
    def test_an_empty_board_still_moves(self):
        """The first minutes of the fair: nobody has set a time and this is the
        only thing on screen that is moving."""
        assert lap_pace(None) == DEFAULT_LAP_SECONDS
        assert lap_pace(0.0) == DEFAULT_LAP_SECONDS

    def test_a_record_sets_the_pace(self):
        assert lap_pace(13.4) == 13.4

    def test_a_hopeless_record_does_not_stop_the_car(self, backdrop):
        """A car that takes a minute to cross the screen has stopped being
        motion, whatever the slowest player managed."""
        lap(backdrop, SLOWEST_LAP_SECONDS, 600.0)
        assert distance_round(backdrop.progress, 0.0) < 0.05


class TestCost:
    def test_the_circuit_is_drawn_once(self, backdrop):
        """The justification for the whole feature: it replaces the flat fill
        the screen was already doing rather than adding work on top of it."""
        screen = pygame.Surface(SCREEN)
        before = backdrop._layer
        backdrop.draw(screen)
        backdrop.draw(screen)
        assert backdrop._layer is before

    def test_the_trail_keeps_no_state(self, backdrop):
        """Sampled backwards along the centreline rather than remembered frame
        by frame, so it cannot drift out of step with the car or grow."""
        screen = pygame.Surface(SCREEN)
        backdrop.draw(screen)
        assert not [name for name in vars(backdrop) if "trail" in name]


class TestDrawing:
    def test_it_draws(self, backdrop):
        screen = pygame.Surface(SCREEN)
        backdrop.draw(screen)

    def test_it_marks_the_screen(self, backdrop):
        """A backdrop that silently drew nothing would look exactly like one
        that was working, until somebody stood in front of the stand."""
        screen = pygame.Surface(SCREEN)
        screen.fill((0, 0, 0))
        backdrop.draw(screen)
        assert pygame.transform.average_color(screen)[:3] != (0, 0, 0)

    def test_it_draws_at_any_screen_size(self, display):
        for size in [(1280, 720), (1710, 1068), (2560, 1440)]:
            Backdrop(size, (14, 16, 22)).draw(pygame.Surface(size))

    def test_the_circuit_fits_the_screen(self):
        left, top, right, bottom = circuit_bounds(SCREEN)
        assert left >= 0 and top >= 0
        assert right <= SCREEN[0] and bottom <= SCREEN[1]


class TestBlend:
    def test_the_ends_are_the_colours_themselves(self):
        assert blend((0, 0, 0), (100, 200, 50), 0.0) == (0, 0, 0)
        assert blend((0, 0, 0), (100, 200, 50), 1.0) == (100, 200, 50)

    def test_it_mixes_in_between(self):
        assert blend((0, 0, 0), (100, 200, 50), 0.5) == (50, 100, 25)

    def test_it_cannot_be_pushed_past_either_end(self):
        """Fades are driven by decaying values that can overshoot slightly, and
        a colour component outside 0-255 is an exception from pygame."""
        assert blend((10, 10, 10), (200, 200, 200), 5.0) == (200, 200, 200)
        assert blend((10, 10, 10), (200, 200, 200), -3.0) == (10, 10, 10)
