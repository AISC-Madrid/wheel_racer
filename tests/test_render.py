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
from wheel_racer.effects import DustCloud, TyreTrail
from wheel_racer.inputs import PreviewFrame
from wheel_racer.login import LoginForm
from wheel_racer.render import Renderer, format_time
from wheel_racer.trackart import GRASS, TARMAC, build_track_layer
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

    def test_draws_the_car_at_every_steering_angle(self, renderer, world):
        """The front wheels are rotated by the steering, so every lock has to
        produce a drawable polygon."""
        for steering in (-1.0, -0.4, 0.0, 0.4, 1.0):
            renderer.draw_world(Car(*world.track.start_pose()), steering)

    def test_draws_the_car_with_effects(self, renderer, world):
        trail, dust = TyreTrail(), DustCloud(seed=1)
        car = Car(*world.track.start_pose())
        for _ in range(30):
            car.x += 4.0
            trail.update(car.x, car.y, 1 / 60)
            dust.update(car.x, car.y, car.heading, 180.0, on_track=False, dt=1 / 60)
        renderer.draw_world(car, 0.3, trail, dust)

    def test_the_scratch_layer_is_reused_not_reallocated(self, renderer, world):
        """Effects need alpha, and a fresh full-screen surface every frame was
        several megabytes a second of churn for an identical result."""
        car = Car(*world.track.start_pose())
        renderer.draw_world(car)
        first = renderer._scratch
        renderer.draw_world(car)
        assert renderer._scratch is first

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


class TestCameraPreviewOnABiggerDisplay:
    """Placement has to survive going fullscreen.

    The bug this exists to stop: the gap between the screen edge and the
    circuit grows with the window while the HUD's margin does not, so past
    about 1.3x the corner became the clear space furthest from the centre and
    the preview went and sat underneath the lap clock.
    """

    # A booth laptop, a MacBook's own panel, and two common external monitors.
    SIZES = [(1280, 720), (1710, 1068), (1920, 1080), (2560, 1440)]
    SOURCE = (240, 180)
    """What the camera hands over, in design pixels."""

    @staticmethod
    def renderer_for(size: tuple[int, int]) -> Renderer:
        """A renderer on an off-screen surface, so each size is independent."""
        return Renderer(pygame.Surface(size), build_world(*size))

    def placement(self, size: tuple[int, int]) -> pygame.Rect:
        renderer = self.renderer_for(size)
        scaled = (round(self.SOURCE[0] * renderer.scale),
                  round(self.SOURCE[1] * renderer.scale))
        return pygame.Rect(renderer._preview_anchor(scaled), scaled)

    @pytest.mark.parametrize("size", SIZES)
    def test_the_preview_never_lands_on_the_hud(self, size):
        renderer = self.renderer_for(size)
        assert self.placement(size).collidelist(renderer._hud_zones()) == -1

    @pytest.mark.parametrize("size", SIZES)
    def test_the_hud_zones_cover_where_the_hud_is_drawn(self, size):
        """The reservation is measured from font metrics rather than from the
        panels themselves, so it has to be checked against the real thing."""
        renderer = self.renderer_for(size)
        renderer.screen.fill((0, 0, 0))
        renderer.draw_hud(run_time=61.5, lap=1, laps_total=2, best=59.9,
                          steering=0.0, on_track=True)

        zones = renderer._hud_zones()
        painted = [(x, y)
                   for x in range(0, size[0], 7) for y in range(0, size[1], 7)
                   if renderer.screen.get_at((x, y))[:3] != (0, 0, 0)]
        assert painted, "the hud drew nothing"
        assert all(pygame.Rect(x, y, 1, 1).collidelist(zones) != -1
                   for x, y in painted)

    def test_it_lands_in_the_same_place_whatever_the_resolution(self):
        """The whole picture — circuit, HUD and now the preview — scales
        together, so the search is looking at the same shape every time and
        settles in the same spot. Only same-shaped screens are compared: a
        different aspect ratio letterboxes the circuit and genuinely moves the
        gaps around it."""
        def fraction(size):
            spot = self.placement(size)
            return (spot.centerx / size[0], spot.centery / size[1])

        reference = fraction((1280, 720))
        for size in [(1920, 1080), (2560, 1440)]:
            assert fraction(size) == pytest.approx(reference, abs=0.02)

    def test_the_preview_grows_with_the_display(self):
        """Blitted at its own fixed width it shrank against everything else,
        until on a booth monitor the thing a player checks when the car will
        not turn was a stamp in the corner."""
        renderer = self.renderer_for((2560, 1440))
        renderer.screen.fill((0, 0, 0))
        rgb = np.full((self.SOURCE[1], self.SOURCE[0], 3), 90, dtype=np.uint8)
        renderer.draw_preview(PreviewFrame(rgb=rgb, left=None, right=None))

        spot = self.placement((2560, 1440))
        # A point beyond where an unscaled preview would have ended, but well
        # inside a scaled one.
        beyond = (spot.left + self.SOURCE[0] + 20, spot.top + self.SOURCE[1] + 20)
        assert spot.collidepoint(beyond)
        assert renderer.screen.get_at(beyond)[:3] != (0, 0, 0)

    @pytest.mark.parametrize("size", SIZES)
    def test_the_preview_still_stays_off_the_track(self, size):
        renderer = self.renderer_for(size)
        spot = self.placement(size)
        for x in np.linspace(spot.left, spot.right, 11):
            for y in np.linspace(spot.top, spot.bottom, 9):
                assert not renderer.world.track.locate(float(x), float(y)).on_track


class TestFormatTime:
    def test_shows_hundredths(self):
        assert format_time(12.345) == "12.35"

    def test_pads_seconds_past_a_minute(self):
        assert format_time(72.5) == "1:12.50"

    def test_shows_a_placeholder_when_there_is_no_time(self):
        assert format_time(None) == "--.--"


class TestLoginPanel:
    """Every shape the sign-in panel takes, drawn once. It is the first thing
    anyone sees, so a crash in one of these states would be a crash on arrival."""

    @pytest.fixture
    def renderer(self, screen, world):
        return Renderer(screen, world)

    @pytest.fixture
    def form(self) -> LoginForm:
        return LoginForm()

    def test_draws_an_empty_form(self, renderer, form):
        renderer.draw_login(form, headline="HAND WHEEL RACER",
                            subhead="sign in to play", hint="ENTER to start")

    def test_draws_a_partly_typed_form(self, renderer, form):
        for character in "Lauren":
            form.handle(pygame.event.Event(pygame.KEYDOWN, key=0, unicode=character))
        renderer.draw_login(form, headline="HAND WHEEL RACER")

    def test_draws_an_error(self, renderer, form):
        form.submit()
        assert form.error
        renderer.draw_login(form, headline="HAND WHEEL RACER")

    def test_draws_a_result(self, renderer, form):
        renderer.draw_login(form, headline="30.22", subhead="lap 1 15.30 · lap 2 14.92",
                            hint="ENTER to replay", celebrate=True)

    def test_draws_the_replay_symbol(self, renderer, form, screen):
        """Drawn rather than typed, because the obvious character for it is
        missing from plenty of monospace fonts and would show as a box."""
        screen.fill((0, 0, 0))
        renderer.draw_login(form, headline="30.22", hint="ENTER to replay", replay=True)
        marked = sum(screen.get_at((x, y))[:3] != (0, 0, 0)
                     for x in range(screen.get_width())
                     for y in range(screen.get_height()))
        assert marked > 0

    def test_the_symbol_makes_room_for_itself(self, renderer, form):
        """It sits beside the hint, so the panel has to widen for it rather
        than letting the icon and the text overlap. The hint here is long
        enough to drive the width — a short one hits the minimum panel width
        and the allowance would never show."""
        hint = "ENTER to replay  ·  or type to sign in and take the wheel yourself"
        plain = renderer._login_width(form, "30.22", "", hint)
        with_icon = renderer._login_width(form, "30.22", "", hint, replay=True)
        assert with_icon > plain
        assert with_icon - plain >= renderer._replay_icon_size()

    def test_a_long_hint_widens_the_panel_rather_than_overflowing(self, renderer, form):
        """A returning player's name and address both land in these lines."""
        short = renderer._login_width(form, "30.22", "", "")
        long = renderer._login_width(
            form, "30.22", "",
            "ENTER to race again as Bartholomew  ·  or type to hand over")
        assert long > short
