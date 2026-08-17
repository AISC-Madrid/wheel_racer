"""Tests for the stuck-player watchdog."""

import math

import pytest

from wheel_racer.recovery import RecoveryMonitor

DT = 1.0 / 60.0
FORWARD = 0.0
BACKWARDS = math.pi
SIDEWAYS = math.pi / 2


@pytest.fixture
def monitor() -> RecoveryMonitor:
    return RecoveryMonitor(off_track_seconds=3.0, backwards_seconds=1.5)


def hold(monitor: RecoveryMonitor, seconds: float, on_track: bool,
         car_heading: float = FORWARD) -> bool:
    """Run the monitor for a while. Returns whether it asked for a respawn."""
    for _ in range(round(seconds / DT)):
        if monitor.update(on_track, car_heading, FORWARD, DT):
            return True
    return False


class TestOffTrack:
    def test_racing_on_the_tarmac_never_respawns(self, monitor):
        assert not hold(monitor, 30.0, on_track=True)

    def test_a_long_time_on_the_grass_respawns(self, monitor):
        assert hold(monitor, 3.5, on_track=False)

    def test_clipping_the_grass_through_a_corner_does_not(self, monitor):
        """The common case. An excursion is a time penalty, not a rescue."""
        assert not hold(monitor, 0.8, on_track=False)

    def test_getting_back_on_track_clears_the_timer(self, monitor):
        hold(monitor, 2.5, on_track=False)
        hold(monitor, 0.1, on_track=True)
        assert monitor.off_track_for == 0.0
        assert not hold(monitor, 2.5, on_track=False)

    def test_the_timer_is_visible_for_a_warning(self, monitor):
        hold(monitor, 1.0, on_track=False)
        assert monitor.off_track_for == pytest.approx(1.0, abs=DT)


class TestFacingBackwards:
    def test_driving_the_wrong_way_respawns(self, monitor):
        assert hold(monitor, 2.0, on_track=True, car_heading=BACKWARDS)

    def test_it_respawns_even_while_on_the_tarmac(self, monitor):
        """Someone who has turned around is lost whatever surface they are on."""
        assert hold(monitor, 2.0, on_track=True, car_heading=BACKWARDS)

    def test_sideways_is_just_a_corner(self, monitor):
        """Cars point across the track constantly. Only the far side counts."""
        assert not hold(monitor, 30.0, on_track=True, car_heading=SIDEWAYS)

    def test_a_brief_slide_the_wrong_way_does_not_respawn(self, monitor):
        assert not hold(monitor, 0.5, on_track=True, car_heading=BACKWARDS)

    def test_turning_back_round_clears_the_timer(self, monitor):
        hold(monitor, 1.0, on_track=True, car_heading=BACKWARDS)
        hold(monitor, 0.1, on_track=True, car_heading=FORWARD)
        assert monitor.backwards_for == 0.0

    def test_the_track_direction_is_what_matters_not_the_screen(self, monitor):
        """A car heading down-screen is going the right way on the back straight."""
        for _ in range(600):
            fired = monitor.update(True, car_heading=math.pi, track_heading=math.pi, dt=DT)
            assert not fired


class TestFiring:
    def test_it_does_not_fire_twice_in_a_row(self, monitor):
        """The caller respawns on a True; a second one would strand the car."""
        assert hold(monitor, 3.5, on_track=False)
        assert not monitor.update(False, FORWARD, FORWARD, DT)

    def test_reset_clears_both_timers(self, monitor):
        hold(monitor, 2.0, on_track=False, car_heading=BACKWARDS)
        monitor.reset()
        assert monitor.off_track_for == 0.0
        assert monitor.backwards_for == 0.0
