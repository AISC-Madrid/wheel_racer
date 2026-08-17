"""Tests for the wrist-angle -> steering mapping."""

import math

import pytest

from wheel_racer.steering import SteeringFilter


def instant(**kwargs) -> SteeringFilter:
    """A filter with smoothing disabled, so we can assert on raw geometry."""
    kwargs.setdefault("smoothing_tau", 0.0)
    return SteeringFilter(**kwargs)


def wrists_at(angle_deg: float, half_span: float = 100.0):
    """Two wrist points on a bar tilted by ``angle_deg`` about the origin."""
    dx = half_span * math.cos(math.radians(angle_deg))
    dy = half_span * math.sin(math.radians(angle_deg))
    return (-dx, -dy), (dx, dy)


class TestGeometry:
    def test_level_bar_is_straight_ahead(self):
        left, right = wrists_at(0.0)
        assert instant().update(left, right, 0.016) == 0.0

    def test_right_wrist_lower_steers_right(self):
        left, right = wrists_at(20.0)  # +y is down, so the right wrist is lower
        assert instant().update(left, right, 0.016) > 0.0

    def test_right_wrist_higher_steers_left(self):
        left, right = wrists_at(-20.0)
        assert instant().update(left, right, 0.016) < 0.0

    def test_point_order_does_not_matter(self):
        """We sort by x rather than trusting MediaPipe's handedness label."""
        left, right = wrists_at(20.0)
        forwards = instant().update(left, right, 0.016)
        backwards = instant().update(right, left, 0.016)
        assert forwards == backwards

    def test_tilt_inside_dead_zone_is_ignored(self):
        left, right = wrists_at(3.0)
        assert instant(dead_zone_deg=4.0).update(left, right, 0.016) == 0.0

    def test_steering_eases_in_from_the_dead_zone_edge(self):
        """Just past the dead zone should be near zero, not a jump to some step."""
        left, right = wrists_at(4.5)
        assert instant(dead_zone_deg=4.0, max_angle_deg=40.0).update(left, right, 0.016) < 0.05

    def test_saturates_at_max_angle(self):
        for angle in (40.0, 60.0, 89.0):
            left, right = wrists_at(angle)
            assert instant(max_angle_deg=40.0).update(left, right, 0.016) == pytest.approx(1.0)

    def test_never_leaves_the_unit_range(self):
        for angle in range(-180, 181, 5):
            left, right = wrists_at(float(angle))
            assert -1.0 <= instant().update(left, right, 0.016) <= 1.0

    def test_coincident_wrists_do_not_blow_up(self):
        """Both landmarks can momentarily collapse onto the same point."""
        assert instant().update((10.0, 10.0), (10.0, 10.0), 0.016) == 0.0

    def test_invert_flips_the_sign(self):
        left, right = wrists_at(20.0)
        normal = instant(invert=False).update(left, right, 0.016)
        flipped = instant(invert=True).update(left, right, 0.016)
        assert flipped == -normal

    def test_reading_is_independent_of_how_wide_the_bar_looks(self):
        """A player standing further back must steer the same as one up close."""
        near = instant().update(*wrists_at(20.0, half_span=200.0), 0.016)
        far = instant().update(*wrists_at(20.0, half_span=50.0), 0.016)
        assert near == pytest.approx(far)

    def test_rejects_a_dead_zone_wider_than_the_lock(self):
        with pytest.raises(ValueError):
            SteeringFilter(dead_zone_deg=40.0, max_angle_deg=40.0)


class TestSmoothing:
    def test_moves_toward_the_target_without_reaching_it_at_once(self):
        steer = SteeringFilter(smoothing_tau=0.08)
        left, right = wrists_at(40.0)
        first = steer.update(left, right, 0.016)
        assert 0.0 < first < 1.0

    def test_converges_on_the_target_when_held(self):
        steer = SteeringFilter(smoothing_tau=0.08)
        left, right = wrists_at(40.0)
        for _ in range(100):
            steer.update(left, right, 0.016)
        assert steer.value == pytest.approx(1.0, abs=1e-3)

    def test_feel_does_not_depend_on_framerate(self):
        """Outdoors the camera framerate wanders, so the same elapsed time must
        give the same steering regardless of how many samples arrived."""
        left, right = wrists_at(40.0)

        fast = SteeringFilter(smoothing_tau=0.08)
        for _ in range(60):
            fast.update(left, right, 1.0 / 60.0)

        slow = SteeringFilter(smoothing_tau=0.08)
        for _ in range(15):
            slow.update(left, right, 1.0 / 15.0)

        assert fast.value == pytest.approx(slow.value, abs=1e-6)


class TestHold:
    def test_hold_keeps_the_last_value(self):
        """A lost hand must not snap the wheel to centre and yank the car."""
        steer = instant()
        steer.update(*wrists_at(30.0), 0.016)
        held = steer.value
        assert steer.hold() == held
        assert steer.hold() == held

    def test_hold_before_any_reading_is_centred(self):
        assert instant().hold() == 0.0

    def test_reset_recentres(self):
        steer = instant()
        steer.update(*wrists_at(30.0), 0.016)
        steer.reset()
        assert steer.value == 0.0
