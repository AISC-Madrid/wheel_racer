"""Tests for the pre-rendered circuit and its surroundings.

Mostly one rule, checked from several directions: the road is on top of the
ground, and the ground never on top of the road. That was a real bug — grass
patches are up to 72px across but only their centre could be tested against the
track, so they spilled over the tarmac and were drawn after it.
"""

import numpy as np
import pygame
import pytest

from wheel_racer import config
from wheel_racer.logo import LOGO_PATH, load_logo
from wheel_racer.trackart import (
    GRASS,
    GRASS_DARK,
    GRASS_SPECKLE,
    LOGO_HEIGHT,
    TARMAC,
    TREE_CANOPY,
    TREE_CANOPY_LIT,
    TREE_SHADOW,
    TYRE_STACK,
    _draw_club_logo,
    _logo_fits,
    _logo_position,
    build_track_layer,
)
from wheel_racer.world import build_world

# Anything that belongs to the field. None of it may appear on the racing line.
GROUND_COLOURS = {
    GRASS, GRASS_DARK, GRASS_SPECKLE,
    TREE_CANOPY, TREE_CANOPY_LIT, TREE_SHADOW,
    TYRE_STACK,
}


@pytest.fixture(scope="module")
def display():
    pygame.init()
    surface = pygame.display.set_mode((config.WINDOW_WIDTH, config.WINDOW_HEIGHT))
    yield surface
    pygame.quit()


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def layer(display, world):
    return build_track_layer(world, display.get_size())


class TestTheRoadIsOnTop:
    def test_nothing_from_the_field_covers_the_racing_line(self, layer, world):
        """Sampled the whole way round the centreline. A patch of grass sitting
        on the tarmac reads as an obstacle, and this game has none."""
        for point in world.track.points:
            colour = layer.get_at((int(point[0]), int(point[1])))[:3]
            assert colour not in GROUND_COLOURS

    def test_nothing_from_the_field_covers_the_rest_of_the_width(self, layer, world):
        """Not just the centreline — anywhere a car can legally be."""
        half_width = world.track.width / 2.0
        for progress in np.linspace(0.0, 1.0, 120, endpoint=False):
            x, y, heading = world.track.pose_at(float(progress))
            across = (np.cos(heading + np.pi / 2), np.sin(heading + np.pi / 2))
            for offset in (-0.75, -0.4, 0.0, 0.4, 0.75):
                px = int(x + across[0] * half_width * offset)
                py = int(y + across[1] * half_width * offset)
                assert layer.get_at((px, py))[:3] not in GROUND_COLOURS

    def test_the_infield_keeps_its_scenery(self, layer, world):
        """Laying the road last must not mean flattening the middle: the old
        order filled the infield with plain green, which would erase it."""
        inside = [layer.get_at((x, y))[:3]
                  for x in range(300, 1000, 7) for y in range(220, 500, 7)]
        assert any(colour != GRASS for colour in inside)

    def test_the_track_is_actually_drawn(self, layer, world):
        x, y, _ = world.track.pose_at(0.4)
        assert layer.get_at((int(x), int(y)))[:3] == TARMAC


class TestTheClubLogo:
    @pytest.fixture
    def mark(self, display, world):
        size = round(LOGO_HEIGHT * world.scale)
        logo = load_logo(size)
        if logo is None:
            pytest.skip("club logo not present in this checkout")
        return logo

    def test_it_finds_a_place_to_stand(self, world, display, mark):
        assert _logo_position(world, mark.get_size(), display.get_size()) is not None

    def test_where_it_stands_is_clear_of_the_track(self, world, display, mark):
        """It is square to the screen while the circuit crosses the start line
        at an angle, so its bounding box reaches further than it looks."""
        spot = _logo_position(world, mark.get_size(), display.get_size())
        assert spot is not None
        assert _logo_fits(world, spot, mark.get_size(), display.get_size())

    def test_it_stays_on_screen(self, world, display, mark):
        spot = _logo_position(world, mark.get_size(), display.get_size())
        assert spot is not None
        assert 0 < spot[0] < display.get_width()
        assert 0 < spot[1] < display.get_height()

    def test_a_mark_too_big_to_fit_is_left_out(self, world, display):
        """Better no logo than one lying across the racing line."""
        enormous = (display.get_width(), display.get_height())
        assert _logo_position(world, enormous, display.get_size()) is None

    def test_it_is_drawn_with_its_transparency_intact(self, world, display, mark):
        """Straight onto the grass, no plate behind it — so the corners of the
        image have to stay green rather than becoming a box."""
        layer = pygame.Surface(display.get_size())
        layer.fill(GRASS)
        _draw_club_logo(layer, world, display.get_size(), [])

        spot = _logo_position(world, mark.get_size(), display.get_size())
        assert spot is not None
        half = mark.get_width() // 2
        corner = (spot[0] - half + 1, spot[1] - mark.get_height() // 2 + 1)
        assert layer.get_at(corner)[:3] == GRASS

    def test_nothing_is_drawn_when_the_logo_is_missing(self, world, display, monkeypatch):
        """No logo means no logo — not a placeholder box on the grass."""
        monkeypatch.setattr("wheel_racer.trackart.load_logo", lambda *args: None)
        layer = pygame.Surface(display.get_size())
        layer.fill(GRASS)
        _draw_club_logo(layer, world, display.get_size(), [])
        assert all(layer.get_at((x, y))[:3] == GRASS
                   for x in range(0, display.get_width(), 13)
                   for y in range(0, display.get_height(), 13))


class TestLogo:
    def test_it_loads_at_the_size_asked_for(self, display):
        if not LOGO_PATH.is_file():
            pytest.skip("club logo not present in this checkout")
        logo = load_logo(40)
        assert logo is not None and logo.get_height() == 40

    def test_it_keeps_its_shape(self, display):
        if not LOGO_PATH.is_file():
            pytest.skip("club logo not present in this checkout")
        small, large = load_logo(40), load_logo(80)
        assert small is not None and large is not None
        assert small.get_width() / 40 == pytest.approx(large.get_width() / 80, abs=0.05)

    def test_it_is_only_read_from_disk_once_per_size(self, display):
        if not LOGO_PATH.is_file():
            pytest.skip("club logo not present in this checkout")
        assert load_logo(40) is load_logo(40)

    def test_the_logo_ships_with_the_repo(self):
        """It lives in assets/ rather than the gitignored resources/, so a
        fresh clone onto the booth laptop still has the club's branding."""
        assert LOGO_PATH.is_file(), f"expected the club logo at {LOGO_PATH}"

    def test_a_missing_logo_is_not_an_error(self, tmp_path):
        """Belt and braces: if it ever goes missing, the booth still runs."""
        assert load_logo(40, tmp_path / "nope.png") is None

    def test_an_unreadable_logo_is_not_an_error(self, tmp_path):
        broken = tmp_path / "broken.png"
        broken.write_bytes(b"this is not a png")
        assert load_logo(40, broken) is None

    def test_it_keeps_its_transparency(self, display):
        """The whole point of drawing it straight onto the grass."""
        if not LOGO_PATH.is_file():
            pytest.skip("club logo not present in this checkout")
        logo = load_logo(60)
        assert logo is not None
        assert logo.get_at((0, 0)).a == 0

    def test_it_always_has_a_real_alpha_channel(self, display):
        """Not `convert_alpha`, which converts to the display's format — where
        that format carries no alpha the background comes back opaque and the
        mark lands on the grass as a solid black square."""
        if not LOGO_PATH.is_file():
            pytest.skip("club logo not present in this checkout")
        logo = load_logo(60)
        assert logo is not None
        assert logo.get_bitsize() == 32
        assert logo.get_flags() & pygame.SRCALPHA
        assert logo.get_masks()[3] != 0
