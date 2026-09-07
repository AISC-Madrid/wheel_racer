"""Tests for writing a file that another process is reading.

These exist because of a crash at a stand: the game rewrites `live.json` twice a
second, the station polls it twice a second, and on Windows `os.replace` refuses
to move a file that anything has open. Twenty seconds in, the two coincided and
the traceback ended the game.

The Windows-only behaviour cannot be produced on Linux, so the retry is tested
by making `os.replace` fail the way Windows fails it. What the tests pin is the
decision: try again for a few milliseconds, and let the caller decide what a
final failure means.
"""

import json
import os

import pytest

from wheel_racer import atomicfile
from wheel_racer.atomicfile import write_json
from wheel_racer.live import LiveChannel, LiveState


class Refusing:
    """`os.replace`, refusing the first `times` calls the way Windows does."""

    def __init__(self, times: int) -> None:
        self.times = times
        self.calls = 0
        self.real = os.replace

    def __call__(self, source, destination):
        self.calls += 1
        if self.calls <= self.times:
            raise PermissionError(5, "Acceso denegado")
        return self.real(source, destination)


class TestWritingIt:
    def test_what_goes_in_comes_out(self, tmp_path):
        write_json(tmp_path / "thing.json", {"hola": 1})
        assert json.loads((tmp_path / "thing.json").read_text()) == {"hola": 1}

    def test_the_directory_is_made_if_it_is_missing(self, tmp_path):
        write_json(tmp_path / "nested" / "deeper" / "thing.json", {"hola": 1})
        assert (tmp_path / "nested" / "deeper" / "thing.json").is_file()

    def test_an_existing_file_is_replaced_whole(self, tmp_path):
        path = tmp_path / "thing.json"
        write_json(path, {"first": True})
        write_json(path, {"second": True})
        assert json.loads(path.read_text()) == {"second": True}


class TestWhenWindowsSaysNo:
    def test_a_busy_destination_is_waited_out(self, tmp_path, monkeypatch):
        """The reader has it open for as long as it takes to parse a few
        hundred bytes. Trying again a moment later is all it needs."""
        refusing = Refusing(times=2)
        monkeypatch.setattr(atomicfile.os, "replace", refusing)

        write_json(tmp_path / "thing.json", {"hola": 1})
        assert refusing.calls == 3
        assert (tmp_path / "thing.json").is_file()

    def test_it_gives_up_eventually_rather_than_hanging(self, tmp_path, monkeypatch):
        monkeypatch.setattr(atomicfile.os, "replace", Refusing(times=99))
        with pytest.raises(PermissionError):
            write_json(tmp_path / "thing.json", {"hola": 1})

    def test_nothing_is_left_behind_when_it_gives_up(self, tmp_path, monkeypatch):
        """A booth laptop running all afternoon must not end the day with a
        directory full of `.live-*.tmp`."""
        monkeypatch.setattr(atomicfile.os, "replace", Refusing(times=99))
        with pytest.raises(PermissionError):
            write_json(tmp_path / "thing.json", {"hola": 1})
        assert list(tmp_path.iterdir()) == []


class TestTheGameSurvivesIt:
    """The crash this was found by, and the rule it established: the person at
    the wheel never finds out that the second screen missed an update."""

    def test_publishing_does_not_raise_when_the_file_cannot_be_written(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(atomicfile.os, "replace", Refusing(times=99))
        channel = LiveChannel(tmp_path / "live.json")
        assert channel.publish(LiveState(state="racing", name="Marta")) is False

    def test_the_next_frame_tries_again(self, tmp_path, monkeypatch):
        """A failed write is not recorded as written, so the update is not held
        back until the heartbeat is due half a second later."""
        refusing = Refusing(times=99)
        monkeypatch.setattr(atomicfile.os, "replace", refusing)
        channel = LiveChannel(tmp_path / "live.json")
        state = LiveState(state="racing", name="Marta")

        channel.publish(state)
        before = refusing.calls
        channel.publish(state)
        assert refusing.calls > before

    def test_it_publishes_normally_once_the_reader_lets_go(
            self, tmp_path, monkeypatch):
        """Three refusals is three skipped frames, not a lost update. The game
        does not wait for the file; it simply offers it again 16ms later."""
        refusing = Refusing(times=3)
        monkeypatch.setattr(atomicfile.os, "replace", refusing)
        channel = LiveChannel(tmp_path / "live.json")
        state = LiveState(state="racing", name="Marta")

        frames = [channel.publish(state, now=100.0 + frame / 60.0)
                  for frame in range(6)]
        assert frames[:3] == [False, False, False]
        assert True in frames
        assert channel.read().name == "Marta"
