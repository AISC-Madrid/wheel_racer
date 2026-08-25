"""Tests for the channel between the game and the second screen.

Two processes, one file, and neither is allowed to take the other down. So what
is worth pinning is mostly what happens when things go wrong: a missing file, a
half-written one, a game that has been closed, a leaderboard older than the
game writing to it. All of those have to come back as "no driver" rather than
as an exception, because the alternative is a dead screen at the stand.
"""

import json
import time

import pytest

from wheel_racer.live import (HEARTBEAT_SECONDS, STALE_AFTER_SECONDS,
                              LiveChannel, LiveState)


@pytest.fixture
def channel(tmp_path) -> LiveChannel:
    return LiveChannel(tmp_path / "live.json")


@pytest.fixture
def reader(channel) -> LiveChannel:
    """A second handle on the same file, as the leaderboard would have."""
    return LiveChannel(channel.path)


def racing(name: str = "Marta", elapsed: float = 5.0) -> LiveState:
    return LiveState(state="racing", name=name, lap=1, laps=2,
                     clock_started_at=time.time() - elapsed)


class TestPublishing:
    def test_what_goes_in_comes_out(self, channel, reader):
        channel.publish(racing())
        state = reader.read()
        assert state is not None
        assert state.state == "racing"
        assert state.name == "Marta"

    def test_a_change_is_written_immediately(self, channel):
        now = time.time()
        assert channel.publish(LiveState(state="idle"), now)
        assert channel.publish(LiveState(state="racing"), now)

    def test_an_unchanged_state_is_not_rewritten(self, channel):
        """`publish` is called every frame. Rewriting the file sixty times a
        second to say the same thing would be pointless disk traffic."""
        now = time.time()
        state = LiveState(state="result", name="Ana")
        assert channel.publish(state, now)
        assert not channel.publish(state, now + HEARTBEAT_SECONDS / 2)

    def test_an_unchanged_state_still_beats(self, channel):
        """The gap since the last write is how the leaderboard tells an idle
        game from a closed one, so silence cannot mean 'nothing changed'."""
        now = time.time()
        state = LiveState(state="result", name="Ana")
        channel.publish(state, now)
        assert channel.publish(state, now + HEARTBEAT_SECONDS * 1.1)

    def test_the_heartbeat_refreshes_the_timestamp(self, channel, reader):
        """A re-published identical state has to come back looking fresh, or
        the leaderboard would call a parked result screen stale."""
        now = time.time()
        state = LiveState(state="result", updated_at=now - 500.0)
        channel.publish(state, now)
        assert reader.read().updated_at == pytest.approx(now)

    def test_clearing_leaves_no_driver(self, channel, reader):
        channel.publish(racing())
        assert reader.read() is not None
        channel.clear()
        assert reader.read() is None

    def test_no_temporary_files_are_left_behind(self, channel):
        for index in range(5):
            channel.publish(LiveState(state="racing", lap=index))
        assert [p.name for p in channel.path.parent.iterdir()] == ["live.json"]


class TestTheClock:
    def test_the_start_time_is_sent_not_the_reading(self, channel, reader):
        """The whole point of the design: the second screen runs its own 60fps
        clock off a couple of writes a second, so a late write cannot make the
        lap timer stutter."""
        channel.publish(racing(elapsed=5.0))
        state = reader.read()

        assert state.elapsed(now=state.clock_started_at + 9.0) == pytest.approx(9.0)
        assert state.elapsed(now=state.clock_started_at + 30.0) == pytest.approx(30.0)

    def test_a_finished_time_does_not_run(self, channel, reader):
        channel.publish(LiveState(state="result", frozen_time=14.21))
        state = reader.read()
        assert state.elapsed() == pytest.approx(14.21)
        assert state.elapsed(now=time.time() + 60) == pytest.approx(14.21)

    def test_no_clock_at_all_reads_as_nothing(self):
        assert LiveState(state="idle").elapsed() is None


class TestWhenThingsGoWrong:
    def test_no_file_means_no_driver(self, reader):
        assert reader.read() is None

    def test_a_half_written_file_means_no_driver(self, channel, reader):
        """The write is atomic so this should not happen — but a full disk or a
        pulled power lead is exactly the kind of thing a booth day produces,
        and it must not be the reason the screen goes down."""
        channel.path.parent.mkdir(parents=True, exist_ok=True)
        channel.path.write_text('{"state": "raci')
        assert reader.read() is None

    def test_something_that_is_not_an_object_means_no_driver(self, channel, reader):
        channel.path.parent.mkdir(parents=True, exist_ok=True)
        channel.path.write_text("[1, 2, 3]")
        assert reader.read() is None

    def test_a_newer_game_does_not_break_an_older_screen(self, channel, reader):
        """Fields this version has never heard of are dropped rather than
        raising, so a leaderboard left running through an update degrades to
        what it understands."""
        channel.path.parent.mkdir(parents=True, exist_ok=True)
        channel.path.write_text(json.dumps({
            "state": "racing", "name": "Javi",
            "sector_times": [4.1, 5.2], "tyre_compound": "soft",
        }))
        state = reader.read()
        assert state is not None and state.name == "Javi"

    def test_a_closed_game_goes_stale(self, channel, reader):
        channel.publish(racing())
        state = reader.read()
        assert not state.is_stale()
        assert state.is_stale(now=time.time() + STALE_AFTER_SECONDS + 1)

    def test_staleness_is_slower_than_the_heartbeat(self):
        """A leaderboard that dropped the driver every time the game stuttered
        would be worse than one that takes a moment to notice a real shutdown."""
        assert STALE_AFTER_SECONDS > HEARTBEAT_SECONDS * 3


class TestPrivacy:
    def test_the_channel_carries_no_email(self, channel):
        """This file feeds a screen pointed at a public stand. The addresses
        the booth collects have no business anywhere near it."""
        assert "email" not in LiveState.__dataclass_fields__

        channel.publish(racing(name="Marta"))
        assert "@" not in channel.path.read_text()


class TestReadingIsCheap:
    def test_an_unchanged_file_is_not_reparsed(self, channel, reader):
        """Polled every frame, so it re-reads on the file's timestamp rather
        than parsing the same JSON sixty times a second."""
        channel.publish(racing())
        first = reader.read()
        assert reader.read() is first

    def test_a_new_write_is_picked_up(self, channel, reader):
        channel.publish(racing(name="Marta"))
        assert reader.read().name == "Marta"

        # Timestamps have limited resolution, so the file is touched forward to
        # make the change unambiguous rather than relying on the clock ticking.
        channel.publish(LiveState(state="racing", name="Javi"))
        import os
        os.utime(channel.path, (time.time() + 5, time.time() + 5))
        assert reader.read().name == "Javi"
