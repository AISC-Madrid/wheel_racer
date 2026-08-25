"""Tests for the player CSV.

This file is the only thing the booth actually takes away from the afternoon,
so the properties worth pinning are about not losing it: one row per person, a
better time replaces a worse one, a worse one never replaces a better, and a
half-written file can never appear.
"""

import csv

import pytest

from wheel_racer.players import FIELDS, PlayerBook, normalise_email


@pytest.fixture
def book(tmp_path) -> PlayerBook:
    return PlayerBook(tmp_path / "players.csv")


class TestRecording:
    def test_a_first_run_is_saved(self, book):
        book.record("Lauren", "lauren@example.com", 31.4)
        assert book.get("lauren@example.com").best_seconds == pytest.approx(31.4)

    def test_a_quicker_run_replaces_it(self, book):
        book.record("Lauren", "lauren@example.com", 31.4)
        book.record("Lauren", "lauren@example.com", 28.9)
        assert book.get("lauren@example.com").best_seconds == pytest.approx(28.9)

    def test_a_slower_run_does_not(self, book):
        """Only the best time is kept, so a bad second go cannot cost someone
        the time they already set."""
        book.record("Lauren", "lauren@example.com", 28.9)
        book.record("Lauren", "lauren@example.com", 34.0)
        assert book.get("lauren@example.com").best_seconds == pytest.approx(28.9)

    def test_a_returning_player_does_not_get_a_second_row(self, book):
        book.record("Lauren", "lauren@example.com", 31.4)
        book.record("Lauren", "lauren@example.com", 28.9)
        assert len(book.all()) == 1

    def test_what_record_returns_is_what_is_stored(self, book):
        """The caller uses this to decide whether to say NEW BEST, so it has to
        match what a later read will say."""
        returned = book.record("Lauren", "lauren@example.com", 30.216666)
        assert returned == book.get("lauren@example.com")

    def test_a_later_spelling_of_a_name_wins(self, book):
        """More likely the correction than the mistake."""
        book.record("Laurne", "lauren@example.com", 31.4)
        book.record("Lauren", "lauren@example.com", 33.0)
        assert book.get("lauren@example.com").name == "Lauren"

    def test_different_people_get_different_rows(self, book):
        book.record("Lauren", "lauren@example.com", 31.4)
        book.record("Ada", "ada@example.com", 29.0)
        assert len(book.all()) == 2


class TestEmailIsTheIdentity:
    def test_case_does_not_split_a_person_in_two(self, book):
        book.record("Lauren", "Lauren@Example.com", 31.4)
        book.record("Lauren", "lauren@example.com", 28.9)
        assert len(book.all()) == 1

    def test_stray_spaces_do_not_either(self, book):
        book.record("Lauren", "  lauren@example.com  ", 31.4)
        assert book.get("lauren@example.com") is not None

    def test_lookup_is_forgiving_the_same_way(self, book):
        book.record("Lauren", "lauren@example.com", 31.4)
        assert book.get("  LAUREN@example.COM ") is not None

    def test_normalising_is_idempotent(self):
        once = normalise_email("  Lauren@Example.com ")
        assert normalise_email(once) == once


class TestTheFileItself:
    def test_it_has_exactly_the_three_agreed_columns(self, book):
        book.record("Lauren", "lauren@example.com", 31.4)
        with book.path.open(newline="", encoding="utf-8") as handle:
            assert next(csv.reader(handle)) == list(FIELDS)

    def test_it_is_created_along_with_its_directory(self, tmp_path):
        book = PlayerBook(tmp_path / "nested" / "deeper" / "players.csv")
        book.record("Lauren", "lauren@example.com", 31.4)
        assert book.path.is_file()

    def test_reading_a_file_that_does_not_exist_yet(self, book):
        assert book.all() == []

    def test_a_broken_row_does_not_take_the_session_down(self, book):
        """One bad line must not stop the game with a queue waiting; losing a
        row is a far smaller failure than losing the afternoon."""
        book.path.parent.mkdir(parents=True, exist_ok=True)
        book.path.write_text(
            "name,email,best_seconds\n"
            "Lauren,lauren@example.com,31.40\n"
            "Broken,not-a-time-row,banana\n"
            "Ada,ada@example.com,29.00\n",
            encoding="utf-8",
        )
        assert {p.email for p in book.all()} == {"lauren@example.com", "ada@example.com"}

    def test_no_leftover_temporary_files(self, book):
        """It is written aside and moved into place, so a crash mid-save leaves
        the previous file rather than a truncated one."""
        book.record("Lauren", "lauren@example.com", 31.4)
        assert [p.name for p in book.path.parent.iterdir()] == ["players.csv"]

    def test_times_survive_a_round_trip_exactly(self, book):
        first = book.record("Lauren", "lauren@example.com", 30.216666)
        reread = PlayerBook(book.path).get("lauren@example.com")
        assert reread == first


class TestLeaderboard:
    def test_quickest_first(self, book):
        book.record("Lauren", "lauren@example.com", 31.4)
        book.record("Ada", "ada@example.com", 29.0)
        book.record("Grace", "grace@example.com", 33.2)
        assert [p.name for p in book.top()] == ["Ada", "Lauren", "Grace"]

    def test_it_can_be_cut_short(self, book):
        for index in range(5):
            book.record(f"P{index}", f"p{index}@example.com", 30.0 + index)
        assert len(book.top(3)) == 3
