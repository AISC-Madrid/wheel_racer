"""Tests for the authored circuit and how it scales to a display.

Some of these assert on the shape of the actual circuit rather than on generic
behaviour. That is deliberate: the circuit is content, and these are the
properties that make it playable. If an edit to CONTROL_POINTS breaks one, the
edit made the track worse, not the test wrong.
"""

import numpy as np
import pytest

from wheel_racer import config, track_data
from wheel_racer.world import build_world, scaled_tuning

# The car cannot take a corner tighter than TOP_SPEED / TURN_RATE, and a corner
# that needs most of the available lock is not the sweeper this game is meant
# to be made of. Kept well under 1.0 so there is always lock in hand to correct
# with — a corner you can only just make is one you cannot recover from.
COMFORTABLE_LOCK = 0.60


@pytest.fixture(scope="module")
def centerline() -> np.ndarray:
    return track_data.design_centerline()


def segment_lengths(points: np.ndarray) -> np.ndarray:
    closed = np.vstack([points, points[:1]])
    return np.hypot(*np.diff(closed, axis=0).T)


class TestCenterline:
    def test_is_evenly_spaced(self, centerline):
        """Checkpoint validation assumes progress is linear in distance driven."""
        spacing = segment_lengths(centerline)
        assert spacing.max() == pytest.approx(track_data.SAMPLE_SPACING, rel=0.05)
        assert spacing.min() == pytest.approx(track_data.SAMPLE_SPACING, rel=0.05)

    def test_is_a_closed_loop(self, centerline):
        """The last point joins back to the first without a jump."""
        gap = np.hypot(*(centerline[0] - centerline[-1]))
        assert gap == pytest.approx(track_data.SAMPLE_SPACING, rel=0.2)

    def test_follows_the_control_points(self, centerline):
        """A spline that wandered far from where it was authored would make the
        control points useless for editing."""
        for control in track_data.CONTROL_POINTS:
            nearest = np.hypot(*(centerline - np.array(control)).T).min()
            assert nearest < 2 * track_data.SAMPLE_SPACING

    def test_rejects_too_few_control_points(self):
        with pytest.raises(ValueError):
            track_data._catmull_rom_loop(np.array([(0.0, 0.0), (1.0, 1.0)]))


class TestCircuitIsPlayable:
    def test_every_corner_is_a_sweeper(self):
        world = build_world(track_data.DESIGN_WIDTH, track_data.DESIGN_HEIGHT)
        tightest = float(world.track.corner_radii().min())
        needed_lock = (config.TOP_SPEED / config.TURN_RATE) / tightest
        assert needed_lock <= COMFORTABLE_LOCK

    def test_the_track_never_runs_into_itself(self, centerline):
        """Two stretches of tarmac merging would open a shortcut between them."""
        count = len(centerline)
        deltas = centerline[:, None, :] - centerline[None, :, :]
        distances = np.hypot(deltas[..., 0], deltas[..., 1])

        index = np.arange(count)
        apart = np.minimum(
            np.abs(index[:, None] - index[None, :]),
            count - np.abs(index[:, None] - index[None, :]),
        )
        distant = apart * track_data.SAMPLE_SPACING > 250.0
        assert distances[distant].min() > config.TRACK_WIDTH

    def test_fits_on_screen_with_room_for_the_tarmac(self, centerline):
        margin = config.TRACK_WIDTH / 2
        assert centerline.min(axis=0)[0] - margin > 0
        assert centerline.min(axis=0)[1] - margin > 0
        assert centerline.max(axis=0)[0] + margin < track_data.DESIGN_WIDTH
        assert centerline.max(axis=0)[1] + margin < track_data.DESIGN_HEIGHT

    def test_uses_most_of_the_screen(self, centerline):
        """A circuit hiding in the middle of the window wastes a small display."""
        span = centerline.max(axis=0) - centerline.min(axis=0)
        assert span[0] > 0.75 * track_data.DESIGN_WIDTH
        assert span[1] > 0.70 * track_data.DESIGN_HEIGHT


class TestScaling:
    def test_the_authored_size_is_scale_one(self):
        world = build_world(track_data.DESIGN_WIDTH, track_data.DESIGN_HEIGHT)
        assert world.scale == pytest.approx(1.0)

    def test_a_bigger_display_scales_everything_together(self):
        small = build_world(1280, 720)
        large = build_world(1920, 1080)
        assert large.scale == pytest.approx(1.5)
        assert large.track.total_length == pytest.approx(1.5 * small.track.total_length)
        assert large.track.width == pytest.approx(1.5 * small.track.width)
        assert large.tuning.top_speed == pytest.approx(1.5 * small.tuning.top_speed)

    def test_lap_time_does_not_change_with_resolution(self):
        """The whole reason speeds scale with the circuit. A leaderboard has to
        mean the same thing on the booth laptop and on a big monitor."""
        for width, height in [(640, 360), (1280, 720), (1920, 1080), (2560, 1440)]:
            world = build_world(width, height)
            assert world.predicted_lap_seconds == pytest.approx(
                build_world(1280, 720).predicted_lap_seconds
            )

    def test_turn_radius_scales_with_the_circuit(self):
        """Corners must stay the same fraction of the track's width, so the
        car's handling does not need retuning per display."""
        small, large = build_world(1280, 720), build_world(1920, 1080)
        assert large.tuning.top_speed / large.tuning.turn_rate == pytest.approx(
            1.5 * small.tuning.top_speed / small.tuning.turn_rate
        )

    def test_turn_rate_is_left_alone(self):
        """It is an angular rate — angles do not care how big the screen is."""
        assert scaled_tuning(3.0).turn_rate == config.TURN_RATE

    def test_a_taller_window_centres_the_circuit_rather_than_stretching_it(self):
        """Letterboxing keeps corner radii honest; stretching would not."""
        world = build_world(1280, 900)
        assert world.scale == pytest.approx(1.0)
        vertical = world.track.points[:, 1]
        slack = (900 - track_data.DESIGN_HEIGHT) / 2
        design = track_data.design_centerline()[:, 1]
        assert vertical.min() == pytest.approx(design.min() + slack)
