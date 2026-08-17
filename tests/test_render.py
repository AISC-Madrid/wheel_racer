"""Rendering smoke tests, against SDL's dummy driver.

These do not check that the game looks good — that is what running it is for.
They check that every drawing path executes, which is worth having because a
rendering crash in a state nobody visits during development (a respawn flash, a
result panel with one lap in it) would otherwise turn up at the booth.
"""

import pygame
import pytest

from wheel_racer import config
from wheel_racer.car import Car
from wheel_racer.render import GRASS, TARMAC, Renderer, build_track_layer, format_time
from wheel_racer.world import build_world


@pytest.fixture(scope="module")
def screen():
    pygame.init()
    surface = pygame.display.set_mode((config.WINDOW_WIDTH, config.WINDOW_HEIGHT))
    yield surface
    pygame.quit()


@pytest.fixture(scope="module")
def world():
    return build_world()


class TestTrackLayer:
    def test_it_covers_the_window(self, world):
        layer = build_track_layer(world, (config.WINDOW_WIDTH, config.WINDOW_HEIGHT))
        assert layer.get_size() == (config.WINDOW_WIDTH, config.WINDOW_HEIGHT)

    def test_the_start_line_sits_on_tarmac(self, world, screen):
        """A sanity check that the drawn band actually follows the centreline
        rather than being offset or inverted."""
        layer = build_track_layer(world, screen.get_size())
        x, y, _ = world.track.start_pose()
        assert layer.get_at((int(x), int(y)))[:3] != GRASS

    def test_the_infield_is_grass(self, world, screen):
        """The middle of the circuit must not be paved, or the shortcut the lap
        validator guards against would simply be a legal line."""
        layer = build_track_layer(world, screen.get_size())
        assert layer.get_at((640, 360))[:3] != TARMAC


class TestRendererStates:
    """Every overlay the game can put on screen, drawn once."""

    @pytest.fixture
    def renderer(self, screen, world):
        return Renderer(screen, world)

    def test_draws_the_car(self, renderer, world):
        renderer.draw_world(Car(*world.track.start_pose()))

    def test_draws_the_hud_on_track(self, renderer):
        renderer.draw_hud(12.5, 1, 2, None, 0.4, on_track=True)

    def test_draws_the_hud_off_track(self, renderer):
        renderer.draw_hud(12.5, 2, 2, 30.1, -0.9, on_track=False)

    def test_draws_the_hud_before_the_clock_starts(self, renderer):
        renderer.draw_hud(None, 1, 2, None, 0.0, on_track=True)

    def test_the_off_track_warning_actually_marks_the_screen(self, renderer, world, screen):
        """The speed drop is easy to miss on a small screen, so the warning
        frame is doing real work — a silently transparent one would be worse
        than none, because we would stop looking for the problem."""
        car = Car(*world.track.start_pose())

        renderer.draw_world(car)
        renderer.draw_hud(1.0, 1, 2, None, 0.0, on_track=True)
        calm = screen.get_at((3, 300))

        renderer.draw_world(car)
        renderer.draw_hud(1.0, 1, 2, None, 0.0, on_track=False)
        warned = screen.get_at((3, 300))

        assert warned != calm

    def test_draws_the_countdown(self, renderer):
        renderer.draw_centre_message("3", huge=True)

    def test_draws_the_attract_screen(self, renderer):
        renderer.draw_centre_message("HAND WHEEL RACER", "hold the bar level")

    def test_draws_a_winning_result(self, renderer):
        renderer.draw_result([15.2, 14.8], best=30.0, is_best=True)

    def test_draws_a_losing_result(self, renderer):
        renderer.draw_result([15.2, 14.8], best=28.0, is_best=False)

    def test_draws_a_partial_result(self, renderer):
        """Reachable if LAPS_PER_RUN is ever changed to one."""
        renderer.draw_result([15.2], best=None, is_best=True)


class TestFormatTime:
    def test_shows_hundredths(self):
        assert format_time(12.345) == "12.35"

    def test_pads_seconds_past_a_minute(self):
        assert format_time(72.5) == "1:12.50"

    def test_shows_a_placeholder_when_there_is_no_time(self):
        assert format_time(None) == "--.--"
