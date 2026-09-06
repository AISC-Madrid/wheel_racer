"""Tests for what the booth keeps on its own disk.

The queue and the two caches are the reason a stand survives losing its wifi,
so what is worth pinning here is almost entirely the awkward cases: a
half-written file, a directory that does not exist yet, a result that arrives
while the network is away, a stale answer landing after a quicker local run.
Every one of those has to end with the run still on disk and the game still
playable.
"""

import json

import pytest

from wheel_racer.station.cache import BoardCache, Roster
from wheel_racer.station.outbox import Outbox, PendingRun


@pytest.fixture
def outbox(tmp_path) -> Outbox:
    return Outbox(tmp_path / "outbox")


def a_run(**overrides) -> PendingRun:
    fields = {
        "name": "Marta Ruiz", "email": "marta@example.com", "seconds": 14.88,
        "station": "booth-1", "terms_version": "2026-01",
        "terms_accepted_at": "2026-03-14T10:00:00+00:00",
    }
    fields.update(overrides)
    return PendingRun(**fields)


class TestOutbox:
    def test_a_queued_run_comes_back_whole(self, outbox):
        outbox.add(a_run())
        (_, payload), = outbox.pending()
        assert payload["email"] == "marta@example.com"
        assert payload["seconds"] == 14.88

    def test_every_run_gets_its_own_id(self):
        assert a_run().id != a_run().id

    def test_an_id_survives_the_round_trip(self, outbox):
        run = a_run()
        outbox.add(run)
        (_, payload), = outbox.pending()
        # This is what makes a blind retry safe. If it changed between queueing
        # and sending, a redelivery would double somebody's runs.
        assert payload["id"] == run.id

    def test_the_directory_is_made_on_first_use(self, tmp_path):
        box = Outbox(tmp_path / "nothing" / "here")
        box.add(a_run())
        assert box.count() == 1

    def test_an_empty_queue_is_not_an_error(self, tmp_path):
        assert Outbox(tmp_path / "never-used").pending() == []
        assert Outbox(tmp_path / "never-used").count() == 0

    def test_sending_one_leaves_the_rest(self, outbox):
        outbox.add(a_run(email="a@example.com"))
        outbox.add(a_run(email="b@example.com"))
        file, _ = outbox.pending()[0]
        outbox.done(file)
        assert outbox.count() == 1

    def test_removing_the_same_run_twice_is_fine(self, outbox):
        outbox.add(a_run())
        file, _ = outbox.pending()[0]
        outbox.done(file)
        outbox.done(file)

    def test_runs_come_back_oldest_first(self, outbox):
        first = outbox.add(a_run(email="first@example.com"))
        second = outbox.add(a_run(email="second@example.com"))
        # Written in order, so the modification times are in order too.
        import os
        os.utime(first, (1000, 1000))
        os.utime(second, (2000, 2000))
        assert [p["email"] for _, p in outbox.pending()] == [
            "first@example.com", "second@example.com"]

    def test_a_corrupt_file_is_skipped_not_raised(self, outbox):
        """One unreadable run must not stop the twenty behind it. Losing a row
        is a smaller failure than losing the afternoon."""
        outbox.add(a_run())
        (outbox.path / "broken.run.json").write_text("{not json", encoding="utf-8")
        assert len(outbox.pending()) == 1

    def test_a_file_holding_something_other_than_a_run_is_skipped(self, outbox):
        (outbox.path).mkdir(parents=True, exist_ok=True)
        (outbox.path / "list.run.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert outbox.pending() == []

    def test_unrelated_files_are_left_alone(self, outbox):
        outbox.add(a_run())
        (outbox.path / "notes.txt").write_text("hello", encoding="utf-8")
        assert len(outbox.pending()) == 1

    def test_no_partial_file_is_ever_visible(self, outbox):
        """The service polls this directory continuously and must never pick
        up a run that is still being written."""
        outbox.add(a_run())
        for file, _ in outbox.pending():
            json.loads(file.read_text(encoding="utf-8"))


class TestBoardCache:
    def test_nothing_cached_before_the_server_answers(self, tmp_path):
        assert BoardCache(tmp_path / "board.json").latest() is None

    def test_what_is_stored_is_what_comes_back(self, tmp_path):
        cache = BoardCache(tmp_path / "board.json")
        cache.store({"standings": [{"name": "Marta"}]})
        assert cache.latest()["standings"][0]["name"] == "Marta"

    def test_the_board_survives_a_restart(self, tmp_path):
        """The kiosk screen has to draw something the instant it opens, even
        if the wifi went away overnight."""
        BoardCache(tmp_path / "board.json").store({"players": 7})
        assert BoardCache(tmp_path / "board.json").latest() == {"players": 7}

    def test_a_corrupt_cache_reads_as_no_cache(self, tmp_path):
        path = tmp_path / "board.json"
        path.write_text("{ half a fi", encoding="utf-8")
        assert BoardCache(path).latest() is None

    def test_age_is_unknown_until_the_server_answers(self, tmp_path):
        assert BoardCache(tmp_path / "board.json").age() is None

    def test_age_counts_from_the_last_answer(self, tmp_path):
        cache = BoardCache(tmp_path / "board.json")
        cache.store({}, now=100.0)
        assert cache.age(now=130.0) == 30.0


class TestRoster:
    @pytest.fixture
    def roster(self, tmp_path) -> Roster:
        return Roster(tmp_path / "known.json")

    def test_an_unknown_address_is_none(self, roster):
        assert roster.get("nobody@example.com") is None
        assert roster.best_for("nobody@example.com") is None

    def test_remembering_and_asking_again(self, roster):
        roster.remember("marta@example.com", "Marta Ruiz", 14.88)
        assert roster.best_for("marta@example.com") == 14.88
        assert roster.get("marta@example.com")["name"] == "Marta Ruiz"

    def test_the_roster_survives_a_restart(self, roster):
        roster.remember("marta@example.com", "Marta", 14.88)
        assert Roster(roster.path).best_for("marta@example.com") == 14.88

    def test_a_quicker_time_replaces_the_old_one(self, roster):
        roster.remember("marta@example.com", "Marta", 14.88)
        roster.remember("marta@example.com", "Marta", 13.50)
        assert roster.best_for("marta@example.com") == 13.50

    def test_a_slower_answer_never_undoes_a_quicker_run(self, roster):
        """A reply about a run from before the last one can arrive after it —
        the queue is asynchronous. It must not put the old time back."""
        roster.remember("marta@example.com", "Marta", 13.50)
        roster.remember("marta@example.com", "Marta", 14.88)
        assert roster.best_for("marta@example.com") == 13.50

    def test_a_name_correction_wins(self, roster):
        roster.remember("marta@example.com", "marta", 14.88)
        roster.remember("marta@example.com", "Marta Ruiz", 14.88)
        assert roster.get("marta@example.com")["name"] == "Marta Ruiz"

    def test_somebody_with_no_time_yet_can_still_be_known(self, roster):
        roster.remember("new@example.com", "Alguien", None)
        assert roster.get("new@example.com") is not None
        assert roster.best_for("new@example.com") is None

    def test_a_corrupt_roster_reads_as_empty(self, tmp_path):
        path = tmp_path / "known.json"
        path.write_text("nonsense", encoding="utf-8")
        assert len(Roster(path)) == 0
