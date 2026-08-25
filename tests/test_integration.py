"""Drives the whole thing, without a window.

Steering, physics, track geometry and lap validation are each covered on their
own elsewhere. This is the test that they agree with one another: a car driven
round the authored circuit by a simple autopilot has to actually complete laps
that the validator accepts, in roughly the time the design predicts.

It is also the fastest way to find out that a change to the track or the tuning
made the circuit undrivable — a real problem that no unit test would notice.
"""

import math

import pytest

from wheel_racer import config
from wheel_racer.car import Car
from wheel_racer.laptimer import LapTimer
from wheel_racer.world import build_world

DT = 1.0 / 60.0
TAU = 2.0 * math.pi

# How far up the road the autopilot looks, and how hard it corrects. Roughly a
# competent-but-not-perfect driver: it holds the racing line but does not cut
# corners, so its times should sit near the predicted floor without beating it.
LOOKAHEAD_PX = 90.0
STEERING_GAIN = 2.5


def autopilot(track, car: Car) -> float:
    """Steer toward a point a little way ahead on the centreline."""
    here = track.locate(car.x, car.y)
    ahead = here.progress + LOOKAHEAD_PX / track.total_length
    target_x, target_y, _ = track.pose_at(ahead)

    wanted = math.atan2(target_y - car.y, target_x - car.x)
    error = (wanted - car.heading + math.pi) % TAU - math.pi
    return max(-1.0, min(1.0, error * STEERING_GAIN))


class Attempt:
    """The result of driving a full run."""

    def __init__(self, laps: list[float], frames: int, off_track_frames: int) -> None:
        self.laps = laps
        self.frames = frames
        self.off_track_frames = off_track_frames

    @property
    def total(self) -> float:
        return sum(self.laps)

    @property
    def time_on_track(self) -> float:
        return 1.0 - self.off_track_frames / self.frames


def drive_a_run(laps_wanted: int = config.LAPS_PER_RUN, limit_seconds: float = 120.0) -> Attempt:
    world = build_world()
    car = Car(*world.track.start_pose())
    timer = LapTimer(config.NUM_CHECKPOINT_GATES, config.LAP_MAX_PROGRESS_STEP)

    now = 0.0
    timer.start(now)
    laps: list[float] = []
    frames = off_track = 0

    while len(laps) < laps_wanted and now < limit_seconds:
        located = world.track.locate(car.x, car.y)
        car.update(autopilot(world.track, car), located.on_track, DT, world.tuning)

        now += DT
        frames += 1
        off_track += not located.on_track

        lap = timer.update(located.progress, now)
        if lap is not None:
            laps.append(lap)
            timer.start(now)

    return Attempt(laps, frames, off_track)


@pytest.fixture(scope="module")
def attempt() -> Attempt:
    return drive_a_run()


class TestTheCircuitIsDrivable:
    def test_the_autopilot_finishes_the_run(self, attempt):
        assert len(attempt.laps) == config.LAPS_PER_RUN

    def test_it_stays_on_the_tarmac(self, attempt):
        """If a clean line cannot be held, the corners are too tight for the
        car — which is a track problem, not a driving one."""
        assert attempt.time_on_track > 0.95

    def test_every_lap_is_accepted_by_the_validator(self, attempt):
        """The anti-cheat must not reject honest driving. A lap only lands in
        this list if all its gates were cleared, in order, without a jump."""
        assert all(lap > 0 for lap in attempt.laps)

    def test_laps_are_consistent(self, attempt):
        """Nothing about the circuit should make one lap wildly unlike another."""
        assert max(attempt.laps) - min(attempt.laps) < 1.5


class TestTheRunLandsNearTheTarget:
    def test_a_good_lap_lands_near_the_centreline_reference(self, attempt):
        """Faster than the centreline time, because the autopilot clips the
        inside of corners — but not faster than the ideal inside line, which
        would mean it was cutting across grass and being credited for it."""
        world = build_world()
        best = min(attempt.laps)
        assert world.ideal_lap_seconds < best < world.predicted_lap_seconds * 1.35

    def test_the_run_is_about_the_length_the_booth_needs(self, attempt):
        """Not exact — a real player is slower than the autopilot. This is the
        check that we are in the right ballpark rather than out by double."""
        assert 0.7 * config.TARGET_RUN_SECONDS < attempt.total < 1.4 * config.TARGET_RUN_SECONDS


class TestGrassCosts:
    def test_leaving_the_line_loses_time(self):
        """Off-track is the only penalty in the game, so it had better bite."""
        world = build_world()
        clean = Car(*world.track.start_pose())
        clean.speed = world.tuning.top_speed
        grassy = Car(*world.track.start_pose())
        grassy.speed = world.tuning.top_speed

        for _ in range(120):
            clean.update(0.0, True, DT, world.tuning)
            grassy.update(0.0, False, DT, world.tuning)

        # Ground covered, not distance along an axis: which way the grid points
        # is a property of the circuit and has nothing to do with whether grass
        # costs time.
        start = world.track.start_pose()[:2]
        assert math.dist((grassy.x, grassy.y), start) < math.dist((clean.x, clean.y), start)
