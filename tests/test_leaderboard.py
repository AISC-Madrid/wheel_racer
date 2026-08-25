"""Tests for the second screen.

Whether it looks good from across a stand is not something a test can answer —
that is what running it is for. What is worth pinning is that every state draws
without falling over (this screen is unattended all afternoon and nobody is
going to be watching a traceback), that the takeover fires exactly once for a
real record, and that no personal data reaches a monitor pointed at the public.
"""

import time

import pygame
import pytest

from wheel_racer.leaderboard import (PODIUM_PLACES, ROWS, TAKEOVER_SECONDS,
                                     BoardSource, LeaderboardScreen, standings)
from wheel_racer.players import PlayerBook
from wheel_racer.live import STALE_AFTER_SECONDS, LiveState
from wheel_racer.players import Player

FIELD = [
    Player("Marta Ruiz", "marta@example.com", 14.21),
    Player("Javi", "javi@example.com", 14.88),
    Player("Lauren Gallego", "lauren@example.com", 15.02),
    Player("Ana", "ana@example.com", 15.41),
]


@pytest.fixture(scope="module")
def display():
    pygame.init()
    pygame.display.set_mode((320, 240))
    yield
    pygame.quit()


@pytest.fixture
def board(display) -> LeaderboardScreen:
    screen = LeaderboardScreen(pygame.Surface((1920, 1080)))
    # Adopt the current leader, as a real screen does on its first frame.
    screen._celebrated_for = "MARTA"
    return screen


def driving(name: str = "Lauren Gallego", elapsed: float = 12.84) -> LiveState:
    return LiveState(state="racing", name=name, lap=2, laps=2,
                     clock_started_at=time.time() - elapsed, personal_best=15.02)


class TestStandings:
    def test_the_leader_has_no_gap_and_everyone_else_does(self):
        rows = standings(FIELD)
        assert rows[0].delta is None
        assert [round(r.delta, 2) for r in rows[1:]] == [0.67, 0.81, 1.20]

    def test_positions_count_from_one(self):
        assert [r.position for r in standings(FIELD)] == [1, 2, 3, 4]

    def test_only_first_names_reach_the_screen(self):
        """Partly because it is all that fits at this size, and partly because
        a full name on a public screen is more identifying than a booth needs
        to be."""
        assert [r.name for r in standings(FIELD)] == ["MARTA", "JAVI", "LAUREN", "ANA"]

    def test_the_current_driver_is_marked(self):
        rows = standings(FIELD, driver="Lauren Gallego")
        assert [r.is_driving for r in rows] == [False, False, True, False]

    def test_nobody_is_marked_when_nobody_is_driving(self):
        assert not any(r.is_driving for r in standings(FIELD, driver=""))

    def test_an_empty_field_is_not_an_error(self):
        """The first minute of the fair, before anyone has played."""
        assert standings([]) == []

    def test_a_nameless_player_does_not_crash_the_board(self):
        """The form should not allow it, but this screen is unattended and a
        blank row is a far smaller failure than a dead monitor."""
        rows = standings([Player("", "x@example.com", 20.0)])
        assert rows[0].name == ""


class TestDrawing:
    """Every state the screen can be in, drawn once."""

    def test_draws_with_a_driver_on_a_flying_lap(self, board):
        board.draw(standings(FIELD, "Lauren Gallego"), driving(), len(FIELD), 1 / 60)

    def test_draws_when_the_driver_has_lost_the_record(self, board):
        board.draw(standings(FIELD), driving("Diego", elapsed=15.9), len(FIELD), 1 / 60)

    def test_draws_the_countdown(self, board):
        board.draw(standings(FIELD), LiveState(state="countdown", name="Ana"),
                   len(FIELD), 1 / 60)

    def test_draws_a_finished_run(self, board):
        board.draw(standings(FIELD),
                   LiveState(state="result", name="Ana", frozen_time=15.41),
                   len(FIELD), 1 / 60)

    def test_draws_with_no_game_running(self, board):
        board.draw(standings(FIELD), None, len(FIELD), 1 / 60)

    def test_draws_before_anyone_has_played(self, board):
        """An empty board is the state the screen opens in, so it cannot be the
        one that was never tried."""
        board.draw([], None, 0, 1 / 60)

    def test_draws_a_full_board(self, board):
        many = [Player(f"Player{i}", f"{i}@example.com", 14.0 + i * 0.3)
                for i in range(20)]
        board.draw(standings(many[:ROWS]), None, len(many), 1 / 60)

    def test_draws_a_very_long_name(self, board):
        """The sign-in form has no reason to stop someone typing one, and an
        ellipsis here would be the one place at the stand where a player's name
        is visibly not good enough."""
        board.draw(standings([Player("Wolfeschlegelsteinhausenberger",
                                     "w@example.com", 14.0)]), None, 1, 1 / 60)

    def test_draws_at_a_different_monitor_size(self, display):
        screen = LeaderboardScreen(pygame.Surface((1280, 720)))
        screen.draw(standings(FIELD), driving(), len(FIELD), 1 / 60)


class TestTheNewLeaderTakeover:
    def test_it_does_not_fire_for_the_leader_already_on_the_board(self, display):
        """Starting the screen up must not announce a record set before it
        booted — which at a fair is every restart after the first."""
        screen = LeaderboardScreen(pygame.Surface((1920, 1080)))
        screen.draw(standings(FIELD), driving(), len(FIELD), 1 / 60)
        assert screen._takeover_until == 0.0

    def test_a_new_leader_takes_the_screen(self, board):
        faster = [Player("Diego", "d@example.com", 13.9)] + FIELD
        board.draw(standings(faster), driving("Diego"), len(faster), 1 / 60)
        assert board._takeover_until > time.time()

    def test_it_only_fires_once(self, board):
        """`publish` heartbeats an unchanged state, so the same record arrives
        again and again — it must not restart the celebration each time."""
        faster = [Player("Diego", "d@example.com", 13.9)] + FIELD
        rows = standings(faster)
        board.draw(rows, driving("Diego"), len(faster), 1 / 60)
        first = board._takeover_until

        for _ in range(30):
            board.draw(rows, driving("Diego"), len(faster), 1 / 60)
        assert board._takeover_until == first

    def test_a_personal_best_that_is_not_a_record_is_not_announced(self, board):
        """Most personal bests are not records. Celebrating every one would
        spend the effect several times an hour and leave nothing for the moment
        that actually matters."""
        improvement = LiveState(state="result", name="Ana", frozen_time=15.0,
                                beat_their_best=True)
        board.draw(standings(FIELD), improvement, len(FIELD), 1 / 60)
        assert board._takeover_until == 0.0

    def test_a_dead_game_cannot_trigger_it(self, board):
        """The board can only change while the game is running, so a new leader
        appearing next to a stale channel means the file was edited or the
        screen was restarted — not that anyone just drove."""
        stale = LiveState(state="racing", name="Diego",
                          updated_at=time.time() - STALE_AFTER_SECONDS - 5)
        faster = [Player("Diego", "d@example.com", 13.9)] + FIELD
        board.draw(standings(faster), stale, len(faster), 1 / 60)
        assert board._takeover_until == 0.0

    def test_it_gives_the_screen_back(self, board):
        faster = [Player("Diego", "d@example.com", 13.9)] + FIELD
        board.draw(standings(faster), driving("Diego"), len(faster), 1 / 60)
        assert board._takeover_until <= time.time() + TAKEOVER_SECONDS

    def test_the_fireworks_go_off_for_it(self, board):
        faster = [Player("Diego", "d@example.com", 13.9)] + FIELD
        rows = standings(faster)
        for _ in range(120):
            board.draw(rows, driving("Diego"), len(faster), 1 / 60)
        assert len(board.fireworks)

    def test_nothing_is_alight_on_an_ordinary_frame(self, board):
        for _ in range(120):
            board.draw(standings(FIELD), driving(), len(FIELD), 1 / 60)
        assert not len(board.fireworks)


def settle(board: LeaderboardScreen, rows, live=None, seconds: float = 1.5) -> None:
    """Draw until every row has arrived where it belongs."""
    for _ in range(round(seconds * 60)):
        board.draw(rows, live, len(rows), 1 / 60)


class TestRowsMoving:
    """A promotion has to be something you watch happen.

    The whole reason for animating is that somebody looking up at the wrong
    moment should still see that the board changed — a tower that simply
    redraws in a new order has told nobody anything.
    """

    def test_the_board_does_not_animate_itself_into_existence(self, board):
        """Opening the screen is not eight simultaneous promotions."""
        board.draw(standings(FIELD), None, len(FIELD), 1 / 60)
        assert all(m.slot == m.target for m in board._motion.values())
        assert all(m.flash == 0.0 for m in board._motion.values())

    def test_a_row_slides_to_its_new_place(self, board):
        settle(board, standings(FIELD))
        faster = [Player("Ana", "ana@example.com", 14.0)] + FIELD[:3]
        rows = standings(sorted(faster, key=lambda p: p.best_seconds))

        board.draw(rows, None, len(rows), 1 / 60)
        ana = board._motion[rows[0].key]
        assert ana.target == 0
        assert ana.slot > 0.0, "should still be on its way, not teleported"

        settle(board, rows)
        assert board._motion[rows[0].key].slot == 0.0

    def test_climbing_into_the_top_three_lights_the_row(self, board):
        settle(board, standings(FIELD))
        promoted = FIELD[:2] + [Player("Luis", "luis@example.com", 14.9)]
        rows = standings(sorted(promoted, key=lambda p: p.best_seconds))

        board.draw(rows, None, len(rows), 1 / 60)
        assert board._motion[rows[2].key].flash > 0.0

    def test_a_new_arrival_straight_into_the_top_three_lights_up(self, board):
        settle(board, standings(FIELD))
        rows = standings(sorted(FIELD + [Player("Diego", "d@example.com", 14.5)],
                                key=lambda p: p.best_seconds))
        board.draw(rows, None, len(rows), 1 / 60)

        diego = next(r for r in rows if r.name == "DIEGO")
        assert board._motion[diego.key].flash > 0.0

    def test_improving_outside_the_top_three_does_not(self, board):
        """Otherwise the effect fires several times an hour and stops meaning
        anything by mid-afternoon."""
        settle(board, standings(FIELD))
        crowd = FIELD + [Player(f"P{i}", f"{i}@example.com", 16.0 + i)
                         for i in range(4)]
        settle(board, standings(sorted(crowd, key=lambda p: p.best_seconds)))

        # P3 improves from last place to fifth — a real gain, no podium.
        crowd[-1] = Player("P3", "3@example.com", 15.3)
        rows = standings(sorted(crowd, key=lambda p: p.best_seconds))
        board.draw(rows, None, len(rows), 1 / 60)

        moved = next(r for r in rows if r.name == "P3")
        assert board._motion[moved.key].flash == 0.0

    def test_the_light_goes_out_on_its_own(self, board):
        settle(board, standings(FIELD))
        promoted = FIELD[:2] + [Player("Luis", "luis@example.com", 14.9)]
        rows = standings(sorted(promoted, key=lambda p: p.best_seconds))
        board.draw(rows, None, len(rows), 1 / 60)

        settle(board, rows, seconds=5.0)
        assert all(m.flash == 0.0 for m in board._motion.values())

    def test_two_players_with_the_same_first_name_are_different_rows(self):
        """Two Martas at a student fair is not a hypothetical, and keyed by
        name the second would inherit the first one's row."""
        rows = standings([Player("Marta Ruiz", "marta.r@example.com", 14.2),
                          Player("Marta Lopez", "marta.l@example.com", 15.1)])
        assert rows[0].name == rows[1].name == "MARTA"
        assert rows[0].key != rows[1].key

    def test_rows_pushed_off_the_board_are_forgotten(self, board):
        """An afternoon's worth of players must not accumulate in memory for
        the sake of eight visible rows."""
        crowd = [Player(f"P{i}", f"{i}@example.com", 14.0 + i * 0.2)
                 for i in range(40)]
        for size in (12, 24, 40):
            everyone = sorted(crowd[:size], key=lambda p: p.best_seconds)
            settle(board, standings(everyone[:ROWS]), seconds=0.3)
        assert len(board._motion) <= ROWS

    def test_the_podium_is_the_top_three(self):
        assert PODIUM_PLACES == 3


class TestReadingThePlayerBook:
    def test_an_unchanged_file_is_not_reparsed(self, tmp_path):
        """The loop wants the standings sixty times a second; the file changes
        once every thirty seconds. Re-reading it every frame was most of what
        this process was doing, on the laptop that is also tracking hands."""
        book = PlayerBook(tmp_path / "players.csv")
        book.record("Marta", "marta@example.com", 14.2)

        source = BoardSource(book)
        assert source.players() is source.players()

    def test_a_new_time_is_picked_up(self, tmp_path):
        book = PlayerBook(tmp_path / "players.csv")
        book.record("Marta", "marta@example.com", 14.2)
        source = BoardSource(book)
        assert len(source.players()) == 1

        book.record("Javi", "javi@example.com", 14.9)
        assert len(source.players()) == 2

    def test_no_file_yet_is_an_empty_board(self, tmp_path):
        """The first minutes of the fair, before anyone has finished a run."""
        assert BoardSource(PlayerBook(tmp_path / "nothing.csv")).players() == []

    def test_the_quickest_come_first(self, tmp_path):
        book = PlayerBook(tmp_path / "players.csv")
        book.record("Slow", "slow@example.com", 20.0)
        book.record("Quick", "quick@example.com", 14.0)
        assert [p.name for p in BoardSource(book).players()] == ["Quick", "Slow"]
