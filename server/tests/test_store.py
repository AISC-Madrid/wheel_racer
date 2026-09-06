"""The data layer, against a real SQLite file.

No mocks. The whole reason to use SQLite here is that a real one costs nothing
to create, and testing the queries against anything else would be testing a
different program.
"""

from __future__ import annotations

import pytest

from wheel_racer_server import store
from wheel_racer_server.db import connect, migrate
from wheel_racer_server.models import StationUpdate, Submission

from conftest import run_payload


def submit(db, **overrides) -> store.Receipt:
    return store.record(db, Submission(**run_payload(**overrides)))


# --- the schema --------------------------------------------------------------

def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "twice.sqlite3"
    first = connect(path)
    assert migrate(first) == migrate(first)
    first.close()

    # And again from a fresh connection, which is what a container restart is.
    second = connect(path)
    assert migrate(second) >= 1
    second.close()


def test_runs_go_when_their_player_does(db):
    submit(db)
    store.erase(db, "marta@example.com")
    assert store.count_runs(db) == 0


# --- recording a run ---------------------------------------------------------

def test_first_run_is_an_improvement(db):
    receipt = submit(db)
    assert receipt.improved
    assert receipt.previous_best is None
    assert receipt.best_seconds == 14.88
    assert receipt.position == 1


def test_a_quicker_second_run_moves_the_best(db):
    submit(db)
    receipt = submit(db, id="run-0002", seconds=13.5)
    assert receipt.improved
    assert receipt.previous_best == 14.88
    assert receipt.best_seconds == 13.5


def test_a_slower_run_is_kept_but_does_not_move_the_best(db):
    submit(db)
    receipt = submit(db, id="run-0002", seconds=20.0)
    assert not receipt.improved
    assert receipt.best_seconds == 14.88
    # The CSV this replaces would have dropped that second run entirely.
    assert store.count_runs(db) == 2


def test_matching_the_best_is_not_an_improvement(db):
    submit(db)
    assert not submit(db, id="run-0002", seconds=14.88).improved


def test_times_are_stored_to_the_hundredth(db):
    assert submit(db, seconds=14.8849).best_seconds == 14.88


def test_the_same_run_twice_changes_nothing(db):
    submit(db)
    repeat = submit(db)
    assert repeat.duplicate
    assert store.count_runs(db) == 1


def test_a_replayed_run_does_not_celebrate_twice(db):
    """The retry queue's whole job is sending things again. A second delivery
    must not report a personal best that was already celebrated and lost."""
    assert submit(db).improved
    assert not submit(db).improved


def test_the_address_is_the_identity(db):
    submit(db, email="marta@example.com")
    submit(db, id="run-0002", email="  MARTA@Example.COM ", seconds=13.0)
    assert store.count_players(db) == 1


def test_a_returning_player_keeps_their_newest_spelling(db):
    submit(db, name="marta")
    submit(db, id="run-0002", name="Marta Ruiz")
    assert store.summary(db, "marta@example.com").name == "Marta Ruiz"


def test_consent_is_stamped_with_every_run(db):
    submit(db)
    row = db.execute("SELECT terms_version, terms_accepted_at FROM players").fetchone()
    assert row["terms_version"] == "2026-01"
    assert row["terms_accepted_at"].startswith("2026-03-14T10:00:00")


# --- the board ---------------------------------------------------------------

def test_the_board_is_quickest_first_with_gaps(db):
    submit(db, id="run-000a", email="a@example.com", name="Ana", seconds=15.0)
    submit(db, id="run-000b", email="b@example.com", name="Beto", seconds=13.0)
    submit(db, id="run-000c", email="c@example.com", name="Cira", seconds=14.0)

    rows = store.standings(db, limit=8)
    assert [row.name for row in rows] == ["Beto", "Cira", "Ana"]
    assert [row.position for row in rows] == [1, 2, 3]
    assert rows[0].gap == 0
    assert rows[2].gap == 2.0


def test_the_board_honours_its_limit(db):
    for index in range(5):
        submit(db, id=f"run-{index:04d}", email=f"p{index}@example.com", seconds=10.0 + index)
    assert len(store.standings(db, limit=3)) == 3


def test_a_tie_is_broken_by_who_got_there_first(db):
    submit(db, id="run-000a", email="a@example.com", name="Ana", seconds=14.0)
    submit(db, id="run-000b", email="b@example.com", name="Beto", seconds=14.0)
    assert [row.name for row in store.standings(db, limit=8)] == ["Ana", "Beto"]


def test_the_board_shows_first_names_only(db):
    submit(db, name="Marta Ruiz Gómez")
    assert store.standings(db, limit=8)[0].name == "Marta"


def test_an_empty_board_is_a_state_not_an_error(db):
    assert store.standings(db, limit=8) == []
    assert store.board(db, 8, 20.0).players == 0


def test_position_counts_everyone_ahead_even_off_the_board(db):
    for index in range(10):
        submit(db, id=f"run-{index:04d}", email=f"p{index}@example.com", seconds=10.0 + index)
    receipt = submit(db, id="run-last", email="last@example.com", seconds=99.0)
    assert receipt.position == 11


# --- names on a public screen ------------------------------------------------

def test_names_that_do_not_clash_are_left_alone():
    people = [("a@x.com", "Marta Ruiz"), ("b@x.com", "Beto Sanz")]
    assert store.public_names(people) == {"a@x.com": "Marta", "b@x.com": "Beto"}


def test_a_clash_gains_an_initial():
    people = [("a@x.com", "Marta Ruiz"), ("b@x.com", "Marta Solana")]
    assert store.public_names(people) == {"a@x.com": "Marta R.", "b@x.com": "Marta S."}


def test_a_clash_with_no_surname_to_give_stays_as_it_is():
    people = [("a@x.com", "Marta"), ("b@x.com", "Marta Solana")]
    assert store.public_names(people) == {"a@x.com": "Marta", "b@x.com": "Marta S."}


def test_only_the_clashing_names_change():
    people = [("a@x.com", "Marta Ruiz"), ("b@x.com", "Marta Solana"),
              ("c@x.com", "Beto Sanz")]
    assert store.public_names(people)["c@x.com"] == "Beto"


def test_two_martas_on_the_board_are_told_apart(db):
    submit(db, id="run-000a", email="a@example.com", name="Marta Ruiz", seconds=13.0)
    submit(db, id="run-000b", email="b@example.com", name="Marta Solana", seconds=14.0)
    assert [row.name for row in store.standings(db, limit=8)] == ["Marta R.", "Marta S."]


@pytest.mark.parametrize("name, expected", [
    ("Marta Ruiz", "Marta"),
    ("  Marta  ", "Marta"),
    ("", ""),
    ("   ", ""),
])
def test_first_name(name, expected):
    assert store.first_name(name) == expected


# --- who is driving ----------------------------------------------------------

def test_a_station_appears_while_it_is_fresh(db):
    store.see_station(db, StationUpdate(station="booth-1", state="racing",
                                        driver="Marta Ruiz", lap=1, laps=2),
                      now=1000.0)
    drivers = store.live(db, stale_after=20.0, now=1005.0)
    assert [d.station for d in drivers] == ["booth-1"]
    assert drivers[0].driver == "Marta"


def test_a_station_that_stopped_writing_drops_off(db):
    store.see_station(db, StationUpdate(station="booth-1", state="racing"), now=1000.0)
    assert store.live(db, stale_after=20.0, now=1030.0) == []


def test_a_station_that_comes_back_is_still_itself(db):
    store.see_station(db, StationUpdate(station="booth-1", state="idle"), now=1000.0)
    store.see_station(db, StationUpdate(station="booth-1", state="racing"), now=2000.0)
    drivers = store.live(db, stale_after=20.0, now=2001.0)
    assert len(drivers) == 1 and drivers[0].state == "racing"


def test_packing_up_clears_the_stand(db):
    store.see_station(db, StationUpdate(station="booth-1", state="idle"), now=1000.0)
    store.forget_station(db, "booth-1")
    assert store.live(db, stale_after=20.0, now=1001.0) == []


# --- moderation and export ---------------------------------------------------

def test_hiding_takes_a_row_off_the_board_without_losing_it(db):
    submit(db, email="rude@example.com", name="Something Unprintable")
    assert store.set_hidden(db, "rude@example.com", True)
    assert store.standings(db, limit=8) == []
    assert store.count_runs(db) == 1


def test_a_hidden_player_has_no_position(db):
    submit(db)
    store.set_hidden(db, "marta@example.com", True)
    assert store.summary(db, "marta@example.com").position is None


def test_hiding_can_be_undone(db):
    submit(db)
    store.set_hidden(db, "marta@example.com", True)
    store.set_hidden(db, "marta@example.com", False)
    assert len(store.standings(db, limit=8)) == 1


def test_hiding_someone_who_is_not_there(db):
    assert not store.set_hidden(db, "nobody@example.com", True)


def test_erasing_removes_the_player_and_the_runs(db):
    submit(db)
    assert store.erase(db, "marta@example.com")
    assert store.summary(db, "marta@example.com") is None
    assert store.count_runs(db) == 0


def test_the_export_carries_the_consent(db):
    submit(db)
    row = store.export(db)[0]
    assert row["email"] == "Marta@Example.com"
    assert row["terms_version"] == "2026-01"
    assert row["runs"] == 1


def test_summary_of_someone_who_never_played(db):
    assert store.summary(db, "nobody@example.com") is None
