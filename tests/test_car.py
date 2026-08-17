"""Tests for the arcade car physics."""

import math

import pytest

from wheel_racer.car import Car, CarTuning

TUNING = CarTuning(top_speed=155.0, grass_speed=60.0, accel=200.0, turn_rate=2.6)
DT = 1.0 / 60.0


def drive(car: Car, seconds: float, steering: float = 0.0, on_track: bool = True) -> None:
    for _ in range(round(seconds / DT)):
        car.update(steering, on_track, DT, TUNING)


class TestThrottle:
    def test_accelerates_from_rest_without_any_input(self):
        """There is no gas pedal — both hands are on the bar."""
        car = Car(0.0, 0.0)
        drive(car, 0.1)
        assert car.speed > 0.0

    def test_settles_at_top_speed_on_tarmac(self):
        car = Car(0.0, 0.0)
        drive(car, 5.0)
        assert car.speed == pytest.approx(TUNING.top_speed)

    def test_slows_toward_grass_speed_off_track(self):
        car = Car(0.0, 0.0, speed=TUNING.top_speed)
        drive(car, 5.0, on_track=False)
        assert car.speed == pytest.approx(TUNING.grass_speed)

    def test_grass_never_stops_the_car(self):
        """Off-track is a slowdown, never a spin-out or a stall — a lost player
        has to be able to steer their way back on."""
        car = Car(0.0, 0.0, speed=TUNING.top_speed)
        drive(car, 30.0, on_track=False)
        assert car.speed == pytest.approx(TUNING.grass_speed)

    def test_recovery_takes_the_time_accel_implies(self):
        car = Car(0.0, 0.0, speed=TUNING.grass_speed)
        expected = (TUNING.top_speed - TUNING.grass_speed) / TUNING.accel
        drive(car, expected + DT)
        assert car.speed == pytest.approx(TUNING.top_speed)


class TestMotion:
    def test_drives_along_positive_x_at_zero_heading(self):
        car = Car(0.0, 0.0, heading=0.0, speed=TUNING.top_speed)
        car.update(0.0, True, DT, TUNING)
        assert car.x == pytest.approx(TUNING.top_speed * DT)
        assert car.y == pytest.approx(0.0)

    def test_positive_steering_turns_right_on_screen(self):
        """Screen y grows downward, so a right turn increases the heading."""
        car = Car(0.0, 0.0, heading=0.0, speed=TUNING.top_speed)
        car.update(1.0, True, DT, TUNING)
        assert car.heading > 0.0

    def test_heading_stays_wrapped(self):
        car = Car(0.0, 0.0, speed=TUNING.top_speed)
        drive(car, 30.0, steering=1.0)
        assert -math.pi <= car.heading <= math.pi

    def test_steering_is_clamped_to_the_unit_range(self):
        sane = Car(0.0, 0.0, speed=TUNING.top_speed)
        absurd = Car(0.0, 0.0, speed=TUNING.top_speed)
        sane.update(1.0, True, DT, TUNING)
        absurd.update(50.0, True, DT, TUNING)
        assert sane.heading == pytest.approx(absurd.heading)


def radius_at(speed: float, tuning: CarTuning) -> float:
    """Turn radius at full lock, holding the car at a given speed."""
    car = Car(0.0, 0.0, heading=0.0, speed=speed)
    on_track = speed >= tuning.top_speed
    car.update(1.0, on_track, DT, tuning)
    return car.speed / (car.heading / DT)


class TestTurnRadius:
    """With `turn_speed_response` at 1.0, angular velocity is proportional to
    speed and the turn radius is the same whatever the car is doing."""

    def radius_at(self, speed: float, on_track: bool) -> float:
        car = Car(0.0, 0.0, heading=0.0, speed=speed)
        car.update(1.0, on_track, DT, TUNING)
        return car.speed / (car.heading / DT)

    def test_radius_is_the_same_on_tarmac_and_on_grass(self):
        assert self.radius_at(TUNING.top_speed, True) == pytest.approx(
            self.radius_at(TUNING.grass_speed, False)
        )

    def test_radius_matches_top_speed_over_turn_rate(self):
        expected = TUNING.top_speed / TUNING.turn_rate
        assert self.radius_at(TUNING.top_speed, True) == pytest.approx(expected)

    def test_rotation_scales_with_speed(self):
        slow = Car(0.0, 0.0, heading=0.0, speed=TUNING.grass_speed)
        fast = Car(0.0, 0.0, heading=0.0, speed=TUNING.top_speed)
        slow.update(1.0, False, DT, TUNING)
        fast.update(1.0, True, DT, TUNING)
        assert fast.heading == pytest.approx(
            slow.heading * TUNING.top_speed / TUNING.grass_speed
        )

    def test_a_car_at_a_standstill_does_not_rotate(self):
        car = Car(0.0, 0.0, heading=0.0, speed=0.0)
        car.update(1.0, True, 0.0, TUNING)
        assert car.heading == 0.0


class TestSteeringAuthorityOnGrass:
    """Why `turn_speed_response` exists.

    A car that keeps a constant turn radius answers the wheel in proportion to
    how fast it is going, so on grass — at 39% of top speed — it turns at 39% of
    the rate. Players read that as the steering having stopped working, right
    when they are trying to get back on the track.
    """

    RESPONSIVE = CarTuning(155.0, 60.0, 200.0, 2.6, turn_speed_response=0.5)
    CONSTANT_RADIUS = CarTuning(155.0, 60.0, 200.0, 2.6, turn_speed_response=1.0)

    def test_a_slow_car_turns_more_tightly(self):
        assert radius_at(self.RESPONSIVE.grass_speed, self.RESPONSIVE) < radius_at(
            self.RESPONSIVE.top_speed, self.RESPONSIVE
        )

    def test_it_turns_more_tightly_than_the_constant_radius_car_would(self):
        """The actual complaint this setting answers: rejoining the circuit was
        too hard because the car would not point at the tarmac."""
        assert radius_at(self.RESPONSIVE.grass_speed, self.RESPONSIVE) < radius_at(
            self.CONSTANT_RADIUS.grass_speed, self.CONSTANT_RADIUS
        )

    def test_top_speed_handling_is_untouched(self):
        """The setting must only change what happens *below* top speed, or it
        would be a back door onto the circuit's corner radii."""
        assert radius_at(self.RESPONSIVE.top_speed, self.RESPONSIVE) == pytest.approx(
            radius_at(self.CONSTANT_RADIUS.top_speed, self.CONSTANT_RADIUS)
        )

    @pytest.mark.parametrize("response", [0.0, 0.5, 1.0])
    def test_a_standstill_never_pivots_whatever_the_setting(self, response):
        """Including 0.0, where the naive formula would give full authority at
        zero speed and let someone spin on the spot waiting for the lights."""
        tuning = CarTuning(155.0, 60.0, 200.0, 2.6, turn_speed_response=response)
        car = Car(0.0, 0.0, heading=0.0, speed=0.0)
        car.update(1.0, True, 0.0, tuning)
        assert car.heading == 0.0

    def test_the_grass_still_costs_time(self):
        """Easier steering on grass must not turn the penalty into a shortcut."""
        tuning = self.RESPONSIVE
        on_grass = Car(0.0, 0.0, heading=0.0, speed=tuning.top_speed)
        on_tarmac = Car(0.0, 0.0, heading=0.0, speed=tuning.top_speed)
        for _ in range(180):
            on_grass.update(0.0, False, DT, tuning)
            on_tarmac.update(0.0, True, DT, tuning)
        assert on_grass.x < on_tarmac.x


class TestReset:
    def test_reset_places_the_car_and_stops_it(self):
        car = Car(0.0, 0.0, speed=TUNING.top_speed)
        drive(car, 1.0, steering=0.5)
        car.reset(10.0, 20.0, heading=math.pi / 2)
        assert car.pose == (10.0, 20.0, math.pi / 2)
        assert car.speed == 0.0

    def test_forward_vector_follows_the_heading(self):
        car = Car(0.0, 0.0, heading=math.pi / 2)
        fx, fy = car.forward
        assert (fx, fy) == (pytest.approx(0.0, abs=1e-9), pytest.approx(1.0))
