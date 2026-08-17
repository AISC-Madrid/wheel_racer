"""Tests for the tyre trail and the dust.

Both exist to make motion visible, and both hold state that ages over time, so
the things worth pinning are that they age at the same rate whatever the
framerate, that they cannot grow without limit over a long booth session, and
that they get wiped between players.
"""

import math

import pygame
import pytest

from wheel_racer.effects import DustCloud, TyreTrail

DT = 1.0 / 60.0


def drive_straight(trail: TyreTrail, distance: float, step: float = 5.0) -> None:
    for index in range(int(distance / step)):
        trail.update(index * step, 0.0, DT)


class TestTyreTrail:
    def test_records_where_the_car_has_been(self):
        trail = TyreTrail()
        drive_straight(trail, 100.0)
        assert len(trail) > 1

    def test_a_stationary_car_does_not_pile_up_marks(self):
        """Otherwise idling on the grid burns a dark hole in the track."""
        trail = TyreTrail()
        for _ in range(600):
            trail.update(100.0, 100.0, DT)
        assert len(trail) == 1

    def test_marks_are_never_closer_than_the_minimum_step(self):
        """Spacing by distance rather than by frame is what keeps a trail drawn
        at 20fps the same line as one drawn at 60."""
        trail = TyreTrail(min_step=4.0)
        for index in range(200):
            trail.update(index * 1.5, 0.0, DT)

        marks = list(trail._marks)
        for before, after in zip(marks, marks[1:]):
            assert math.dist((before.x, before.y), (after.x, after.y)) >= 4.0 - 1e-9

    def test_the_trail_covers_the_same_ground_at_any_framerate(self):
        """Counts differ once a single frame moves further than the spacing,
        but the line drawn through them is the same length either way."""
        fast, slow = TyreTrail(), TyreTrail()
        for index in range(120):
            fast.update(index * 2.0, 0.0, 1 / 120)
        for index in range(30):
            slow.update(index * 8.0, 0.0, 1 / 30)

        def span(trail):
            marks = list(trail._marks)
            return marks[-1].x - marks[0].x

        assert span(fast) == pytest.approx(span(slow), abs=8.0)

    def test_old_marks_expire(self):
        trail = TyreTrail(seconds=0.5)
        drive_straight(trail, 200.0)
        recorded = len(trail)
        for _ in range(60):  # one second of standing still
            trail.update(1000.0, 1000.0, DT)
        assert len(trail) < recorded

    def test_it_cannot_grow_without_limit(self):
        """A booth session is hours long; the trail must not be a slow leak."""
        trail = TyreTrail(max_points=50)
        drive_straight(trail, 100_000.0)
        assert len(trail) <= 50

    def test_clearing_wipes_it(self):
        """One player's line must not be on the track behind the next one."""
        trail = TyreTrail()
        drive_straight(trail, 200.0)
        trail.clear()
        assert len(trail) == 0

    def test_drawing_an_empty_trail_is_harmless(self):
        surface = pygame.Surface((100, 100), pygame.SRCALPHA)
        TyreTrail().draw(surface)

    def test_it_draws_something(self):
        surface = pygame.Surface((200, 200), pygame.SRCALPHA)
        trail = TyreTrail()
        for index in range(20):
            trail.update(10.0 + index * 5.0, 100.0, DT)
        trail.draw(surface)
        assert surface.get_at((60, 100)).a > 0


class TestDustCloud:
    def test_no_dust_on_the_tarmac(self):
        dust = DustCloud(seed=1)
        for _ in range(60):
            dust.update(100.0, 100.0, 0.0, 180.0, on_track=True, dt=DT)
        assert len(dust) == 0

    def test_dust_off_the_tarmac(self):
        dust = DustCloud(seed=1)
        for _ in range(30):
            dust.update(100.0, 100.0, 0.0, 180.0, on_track=False, dt=DT)
        assert len(dust) > 0

    def test_a_stopped_car_throws_up_nothing(self):
        dust = DustCloud(seed=1)
        for _ in range(60):
            dust.update(100.0, 100.0, 0.0, 0.0, on_track=False, dt=DT)
        assert len(dust) == 0

    def test_the_plume_is_the_same_at_any_framerate(self):
        """Emitting per frame rather than per second would make the dust thin
        out exactly when the game slows down, which is backwards."""
        fast, slow = DustCloud(seed=2), DustCloud(seed=2)
        for _ in range(120):
            fast.update(0.0, 0.0, 0.0, 180.0, on_track=False, dt=1 / 120)
        for _ in range(30):
            slow.update(0.0, 0.0, 0.0, 180.0, on_track=False, dt=1 / 30)
        assert abs(len(fast) - len(slow)) <= 2

    def test_grains_are_thrown_behind_the_car(self):
        dust = DustCloud(seed=3)
        for _ in range(20):  # heading 0 is along +x, so dust goes to -x
            dust.update(0.0, 0.0, 0.0, 180.0, on_track=False, dt=DT)
        assert all(grain.x <= 1.0 for grain in dust._grains)

    def test_grains_expire(self):
        dust = DustCloud(seconds=0.2, seed=4)
        for _ in range(20):
            dust.update(0.0, 0.0, 0.0, 180.0, on_track=False, dt=DT)
        assert len(dust) > 0
        for _ in range(60):
            dust.update(0.0, 0.0, 0.0, 180.0, on_track=True, dt=DT)
        assert len(dust) == 0

    def test_it_cannot_grow_without_limit(self):
        dust = DustCloud(maximum=25, seed=5)
        for _ in range(2000):
            dust.update(0.0, 0.0, 0.0, 180.0, on_track=False, dt=DT)
        assert len(dust) <= 25

    def test_clearing_wipes_it(self):
        dust = DustCloud(seed=6)
        for _ in range(30):
            dust.update(0.0, 0.0, 0.0, 180.0, on_track=False, dt=DT)
        dust.clear()
        assert len(dust) == 0

    def test_it_draws_something(self):
        surface = pygame.Surface((200, 200), pygame.SRCALPHA)
        dust = DustCloud(seed=7)
        for _ in range(30):
            dust.update(100.0, 100.0, math.pi, 180.0, on_track=False, dt=DT)
        dust.draw(surface)
        assert any(surface.get_at((x, y)).a > 0
                   for x in range(90, 160) for y in range(70, 130))
