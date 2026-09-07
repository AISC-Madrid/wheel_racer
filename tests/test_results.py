"""Tests for how a run gets saved and how a record gets found.

This is the module that replaced the CSV, and the properties worth pinning are
the ones that made the replacement worth doing:

  * saving is a file write, so it works with no station and no network at all;
  * asking is allowed to fail, and failing means "somebody new" rather than
    an exception in front of a queue;
  * a second run five minutes later is still measured against the first, even
    if nothing has reached the server in between.

The station is stood up for real in a couple of these, on loopback, because
"the game can talk to the station" is exactly the kind of claim that is worth
more when it is not mocked.
"""

import json
from pathlib import Path

import pytest

from wheel_racer.results import Player, Results, normalise_email
from wheel_racer.station.cache import BoardCache, Roster
from wheel_racer.station.outbox import Outbox
from wheel_racer.station.server import Kiosk

NAME = "Marta Ruiz"
EMAIL = "Marta@Example.com"


@pytest.fixture
def results(tmp_path) -> Results:
    """A booth with nowhere to send anything — the offline case, which is the
    one that has to work."""
    return Results(outbox=Outbox(tmp_path / "outbox"),
                   roster=Roster(tmp_path / "known.json"),
                   # No station at all, which is what `--no-station` sets up.
                   kiosk_port=0, station="booth-test")


@pytest.fixture
def orphaned(tmp_path) -> Results:
    """A booth pointed at a station that is not there — the crashed case.

    Different from having no station: this one has to find out, and finding out
    is what costs time.
    """
    return Results(outbox=Outbox(tmp_path / "outbox"),
                   roster=Roster(tmp_path / "known.json"),
                   kiosk_port=1, station="booth-test")


class TestSaving:
    def test_a_run_is_queued(self, results):
        results.record(NAME, EMAIL, 14.88)
        (_, payload), = results.outbox.pending()
        assert payload["seconds"] == 14.88
        assert payload["station"] == "booth-test"

    def test_saving_works_with_no_station_and_no_network(self, results):
        """The whole point of writing to disk. Nothing about this call can
        fail in a way the person at the wheel would recognise."""
        stored = results.record(NAME, EMAIL, 14.88)
        assert stored == Player(name=NAME, email="marta@example.com",
                                best_seconds=14.88)

    def test_the_address_is_normalised_on_the_way_out(self, results):
        results.record(NAME, "  MARTA@Example.COM ", 14.88)
        (_, payload), = results.outbox.pending()
        assert payload["email"] == "marta@example.com"

    def test_consent_travels_with_the_run(self, results):
        from datetime import datetime, timezone

        agreed = datetime(2026, 3, 14, 10, 0, tzinfo=timezone.utc)
        results.record(NAME, EMAIL, 14.88, accepted_at=agreed)
        (_, payload), = results.outbox.pending()
        assert payload["terms_accepted_at"].startswith("2026-03-14T10:00")
        assert payload["terms_version"]

    def test_times_are_kept_to_the_hundredth(self, results):
        assert results.record(NAME, EMAIL, 14.8849).best_seconds == 14.88

    def test_a_quicker_run_moves_the_best(self, results):
        results.record(NAME, EMAIL, 14.88)
        assert results.record(NAME, EMAIL, 13.5).best_seconds == 13.5

    def test_a_slower_run_does_not(self, results):
        results.record(NAME, EMAIL, 14.88)
        assert results.record(NAME, EMAIL, 20.0).best_seconds == 14.88

    def test_every_run_is_kept_not_just_the_best(self, results):
        """The CSV threw away everything that was not somebody's record. The
        server wants all of them, and the queue is what carries them."""
        results.record(NAME, EMAIL, 14.88)
        results.record(NAME, EMAIL, 20.0)
        assert results.queued == 2

    def test_each_run_is_queued_under_its_own_id(self, results):
        results.record(NAME, EMAIL, 14.88)
        results.record(NAME, EMAIL, 15.0)
        ids = {payload["id"] for _, payload in results.outbox.pending()}
        assert len(ids) == 2


class TestAskingWithNoStation:
    def test_somebody_new_has_no_record(self, results):
        assert results.best_for("nobody@example.com") is None

    def test_somebody_who_played_here_is_remembered(self, results):
        """A second go five minutes later, with the wifi still down."""
        results.record(NAME, EMAIL, 14.88)
        assert results.best_for(EMAIL) == 14.88

    def test_the_address_is_matched_the_way_the_server_matches_it(self, results):
        results.record(NAME, EMAIL, 14.88)
        assert results.best_for("  marta@EXAMPLE.com  ") == 14.88

    def test_no_station_at_all_is_not_an_error(self, results):
        assert results.best_for("anyone@example.com") is None

    def test_a_dead_station_is_not_an_error(self, orphaned):
        """Nothing is listening on the port this fixture uses. A sign-in has to
        carry on regardless — this is what happens if the station crashed."""
        assert orphaned.best_for("anyone@example.com") is None

    def test_a_dead_station_is_only_discovered_once(self, orphaned):
        """Refusing a connection is not free — on Windows a closed loopback
        port takes about two seconds to say so — and without this every
        sign-in for the rest of the afternoon would pay it again."""
        import time as clock

        orphaned.best_for("first@example.com")
        started = clock.perf_counter()
        for index in range(5):
            orphaned.best_for(f"later{index}@example.com")
        assert clock.perf_counter() - started < 0.1

    def test_somebody_known_never_troubles_the_station_at_all(self, results):
        """The common case at a stand: they played twenty minutes ago."""
        import time as clock

        results.record(NAME, EMAIL, 14.88)
        started = clock.perf_counter()
        assert results.best_for(EMAIL) == 14.88
        assert clock.perf_counter() - started < 0.1


class TestAskingAStation:
    @pytest.fixture
    def wired(self, tmp_path):
        """A real station on loopback, and a booth pointed at it."""
        roster = Roster(tmp_path / "known.json")
        kiosk = Kiosk(board=BoardCache(tmp_path / "board.json"), roster=roster,
                      client=None, port=0)
        kiosk.start()
        booth = Results(outbox=Outbox(tmp_path / "outbox"),
                        roster=Roster(tmp_path / "unused.json"),
                        kiosk_port=kiosk.port, station="booth-test")
        yield booth, roster
        kiosk.stop()

    def test_a_record_the_station_knows_about_comes_back(self, wired):
        """The case the rewrite exists for: this laptop has never seen them,
        and the station has — from the server, or from an earlier stand."""
        booth, roster = wired
        roster.remember("far@example.com", "Lejos", 12.0)
        assert booth.best_for("far@example.com") == 12.0

    def test_somebody_the_station_does_not_know_is_new(self, wired):
        booth, _ = wired
        assert booth.best_for("nobody@example.com") is None

    def test_a_player_with_no_time_yet_is_not_given_one(self, wired):
        booth, roster = wired
        roster.remember("seen@example.com", "Visto", None)
        assert booth.best_for("seen@example.com") is None


class TestNormalisingAddresses:
    @pytest.mark.parametrize("typed, stored", [
        ("Marta@Example.com", "marta@example.com"),
        ("  marta@example.com  ", "marta@example.com"),
        ("MARTA@EXAMPLE.COM", "marta@example.com"),
    ])
    def test_the_same_person_is_the_same_person(self, typed, stored):
        assert normalise_email(typed) == stored

    def test_it_matches_what_the_server_does(self):
        """Duplicated on purpose — the game cannot import the server — and the
        two have to agree, or a returning player stops being recognised as
        themselves. Checked against the source rather than by importing it,
        because the server is an optional extra the game never installs."""
        source = (Path(__file__).resolve().parents[1] / "server" /
                  "wheel_racer_server" / "store.py").read_text(encoding="utf-8")
        assert "return email.strip().lower()" in source


class TestOnDisk:
    def test_a_queued_run_is_readable_json(self, results):
        """Somebody at a stand should be able to look at the queue with `cat`
        and see what is waiting."""
        results.record(NAME, EMAIL, 14.88)
        file, _ = results.outbox.pending()[0]
        assert json.loads(file.read_text(encoding="utf-8"))["name"] == NAME

    def test_nothing_is_written_until_somebody_finishes_a_run(self, results):
        assert results.queued == 0
