"""Tests for the centreline geometry."""

import math

import pytest

from wheel_racer.track import Track

# A 400x400 square lap, so every arc length is easy to work out by hand.
# Segments, in order: bottom-left corner rightwards, then down, then left, then
# back up. Total centreline length 1600.
SQUARE = [(0.0, 0.0), (400.0, 0.0), (400.0, 400.0), (0.0, 400.0)]
WIDTH = 60.0


@pytest.fixture
def track() -> Track:
    return Track(SQUARE, width=WIDTH)


class TestConstruction:
    def test_closes_the_loop_automatically(self, track):
        assert track.total_length == pytest.approx(1600.0)

    def test_a_repeated_closing_point_is_ignored(self):
        """Authoring a centreline by hand, it is natural to close it manually."""
        explicit = Track([*SQUARE, (0.0, 0.0)], width=WIDTH)
        assert explicit.total_length == pytest.approx(1600.0)

    def test_duplicate_vertices_are_dropped(self):
        """A duplicate would be a zero-length segment, and a division by zero."""
        duplicated = Track(
            [(0.0, 0.0), (0.0, 0.0), (400.0, 0.0), (400.0, 400.0), (0.0, 400.0)],
            width=WIDTH,
        )
        assert duplicated.total_length == pytest.approx(1600.0)
        assert len(duplicated.points) == 4

    def test_rejects_a_degenerate_centreline(self):
        with pytest.raises(ValueError):
            Track([(0.0, 0.0), (100.0, 0.0)], width=WIDTH)

    def test_rejects_a_non_positive_width(self):
        with pytest.raises(ValueError):
            Track(SQUARE, width=0.0)

    def test_rejects_points_that_are_not_pairs(self):
        with pytest.raises(ValueError):
            Track([(0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (2.0, 2.0, 2.0)],  # type: ignore[list-item]
                  width=WIDTH)


class TestOnTrack:
    def test_the_centreline_itself_is_on_track(self, track):
        located = track.locate(200.0, 0.0)
        assert located.on_track
        assert located.distance == pytest.approx(0.0)

    def test_just_inside_the_edge_is_on_track(self, track):
        assert track.locate(200.0, WIDTH / 2 - 1).on_track

    def test_just_outside_the_edge_is_grass(self, track):
        assert not track.locate(200.0, WIDTH / 2 + 1).on_track

    def test_the_infield_is_grass(self, track):
        """The whole point: you cannot drive across the middle for free."""
        assert not track.locate(200.0, 200.0).on_track

    def test_width_is_measured_either_side_of_the_centreline(self, track):
        assert track.locate(200.0, 20.0).on_track
        assert track.locate(200.0, -20.0).on_track


class TestProgress:
    def test_progress_is_arc_length_around_the_lap(self, track):
        assert track.locate(200.0, 0.0).progress == pytest.approx(200.0 / 1600.0)

    def test_progress_covers_the_closing_segment(self, track):
        """The segment from the last authored point back to the first."""
        assert track.locate(0.0, 200.0).progress == pytest.approx(1400.0 / 1600.0)

    def test_progress_starts_at_zero_on_the_start_line(self, track):
        assert track.locate(0.0, 0.0).progress == pytest.approx(0.0)

    def test_progress_stays_in_the_unit_interval(self, track):
        for x, y in [(0, 0), (400, 0), (400, 400), (0, 400), (399, 399), (-50, -50)]:
            assert 0.0 <= track.locate(float(x), float(y)).progress < 1.0

    def test_progress_increases_while_driving_forward(self, track):
        """Sampled all the way round the bottom edge and into the first corner."""
        readings = [track.locate(float(x), 0.0).progress for x in range(0, 400, 20)]
        assert readings == sorted(readings)

    def test_offset_from_the_line_does_not_change_progress(self, track):
        """Progress is measured along the centreline, not along the car's path,
        so the racing line a player takes cannot inflate it."""
        on_line = track.locate(200.0, 0.0).progress
        off_line = track.locate(200.0, 25.0).progress
        assert on_line == pytest.approx(off_line)


class TestPose:
    def test_start_pose_faces_down_the_first_segment(self, track):
        x, y, heading = track.start_pose()
        assert (x, y) == pytest.approx((0.0, 0.0))
        assert heading == pytest.approx(0.0)

    def test_pose_at_a_quarter_lap_is_the_first_corner(self, track):
        x, y, heading = track.pose_at(0.25)
        assert (x, y) == pytest.approx((400.0, 0.0))
        assert heading == pytest.approx(math.pi / 2)  # +y is down the screen

    def test_pose_at_wraps_past_a_full_lap(self, track):
        assert track.pose_at(1.25) == pytest.approx(track.pose_at(0.25))

    def test_located_heading_points_along_the_track(self, track):
        assert track.locate(200.0, 10.0).heading == pytest.approx(0.0)

    def test_locate_gives_a_pose_to_respawn_onto(self, track):
        """Lost in the infield, the car has to be put back somewhere sensible."""
        located = track.locate(200.0, 150.0)
        assert located.pose == pytest.approx((200.0, 0.0, 0.0))


class TestNearestSegmentChoice:
    def test_picks_the_nearer_of_two_candidate_segments(self, track):
        """Near a corner, two segments compete for the projection."""
        located = track.locate(390.0, 10.0)
        assert located.distance == pytest.approx(10.0)

    def test_projection_clamps_to_the_segment_ends(self, track):
        """Beyond the corner, the closest point is the corner itself."""
        located = track.locate(450.0, -50.0)
        assert (located.x, located.y) == pytest.approx((400.0, 0.0))
