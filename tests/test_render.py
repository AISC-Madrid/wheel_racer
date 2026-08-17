"""Rendering smoke tests, against SDL's dummy driver.

These do not check that the game looks good — that is what running it is for.
They check that every drawing path executes, which is worth having because a
rendering crash in a state nobody visits during development (a respawn flash, a
result panel with one lap in it) would otherwise turn up at the booth.
"""

import numpy as np
import pygame
import pytest

from wheel_racer import config
from wheel_racer.car import Car
from wheel_racer.inputs import PreviewFrame
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


class TestCameraPreview:
    """The picture-in-picture. Without it a player has no way to tell a car
    that will not turn from a camera that cannot see them."""

    @pytest.fixture
    def renderer(self, screen, world):
        return Renderer(screen, world)

    @staticmethod
    def frame(left=None, right=None) -> PreviewFrame:
        rgb = np.full((135, 240, 3), 90, dtype=np.uint8)
        return PreviewFrame(rgb=rgb, left=left, right=right)

    def test_no_camera_draws_nothing(self, renderer):
        renderer.draw_preview(None)

    def test_draws_the_image_with_both_wrists(self, renderer):
        renderer.draw_preview(self.frame(left=(60.0, 70.0), right=(180.0, 90.0)))

    def test_draws_a_warning_when_the_hands_are_lost(self, renderer):
        renderer.draw_preview(self.frame())

    def test_a_half_sighting_counts_as_no_hands(self, renderer):
        """One wrist is not enough to steer by, so it must not draw as if it were."""
        assert not self.frame(left=(60.0, 70.0)).has_hands
        renderer.draw_preview(self.frame(left=(60.0, 70.0)))

    # 4:3 is what a 640x480 webcam actually gives; 16:9 is what a widescreen one
    # would. Both have to place, and the taller one is the harder case.
    PREVIEW_SIZES = [(240, 180), (240, 135), (320, 240)]

    @pytest.mark.parametrize("size", PREVIEW_SIZES)
    def test_the_preview_never_covers_the_track(self, renderer, world, size):
        """The circuit fills the window, so every screen corner has tarmac in
        it — the panel has to find a gap rather than assume one. A preview
        sitting over the racing line would hide the car behind it."""
        left, top = renderer._preview_anchor(size)

        for x in np.linspace(left, left + size[0], 11):
            for y in np.linspace(top, top + size[1], 9):
                assert not world.track.locate(float(x), float(y)).on_track

    def test_the_preview_keeps_clear_of_the_centre_overlays(self, renderer, screen):
        """Countdown and result panels are drawn in the middle of the screen."""
        left, top = renderer._preview_anchor((240, 135))
        centre_x, centre_y = screen.get_width() / 2, screen.get_height() / 2
        assert not (left < centre_x < left + 240 and top < centre_y < top + 135)

    def test_the_anchor_is_only_worked_out_once(self, renderer):
        assert renderer._preview_anchor((240, 135)) == renderer._preview_anchor((240, 135))

    def test_the_preview_is_drawn_where_the_anchor_says(self, renderer, screen):
        left, top = renderer._preview_anchor((240, 135))
        screen.fill((0, 0, 0))
        renderer.draw_preview(self.frame(left=(60.0, 70.0), right=(180.0, 90.0)))
        assert screen.get_at((left + 120, top + 67)) != pygame.Color(0, 0, 0)

    def test_the_whole_preview_fits_on_screen(self, renderer, screen):
        left, top = renderer._preview_anchor((240, 135))
        assert 0 <= left and left + 240 <= screen.get_width()
        assert 0 <= top and top + 135 <= screen.get_height()


class TestFormatTime:
    def test_shows_hundredths(self):
        assert format_time(12.345) == "12.35"

    def test_pads_seconds_past_a_minute(self):
        assert format_time(72.5) == "1:12.50"

    def test_shows_a_placeholder_when_there_is_no_time(self):
        assert format_time(None) == "--.--"
