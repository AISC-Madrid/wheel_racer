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

from wheel_racer.leaderboard import (ROWS, TAKEOVER_SECONDS, LeaderboardScreen,
                                     standings)
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
