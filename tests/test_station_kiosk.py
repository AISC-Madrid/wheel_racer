"""Tests for the little server the stand's screen is pointed at.

This one does open a socket, on loopback and on a port the operating system
picks. It is worth it: what is being tested is that a browser gets a page and
that the game gets an answer, and both of those are HTTP whether the test likes
it or not.

The case that matters most is the last one in each group — what happens when
the VPS cannot be reached. The whole reason this server exists instead of
pointing the kiosk browser straight at the internet is that a stand must keep
showing a board through the twenty minutes the venue's wifi is away.
"""

import json
import urllib.error
import urllib.request

import pytest

from wheel_racer.station.cache import BoardCache, Roster
from wheel_racer.station.client import Unreachable
from wheel_racer.station.outbox import Outbox
from wheel_racer.station.server import Kiosk

A_BOARD = {"standings": [{"position": 1, "name": "Marta", "seconds": 13.5,
                          "gap": 0.0}],
           "live": [], "players": 9, "runs": 20}


class FakeServer:
    """Whatever the VPS would have said."""

    base_url = "https://racer.example.com"

    def __init__(self, knows: dict | None = None, reachable: bool = True) -> None:
        self.knows = knows or {}
        self.reachable = reachable
        self.asked: list[str] = []
        self.timeouts: list[float | None] = []

    def lookup(self, email: str, timeout: float | None = None) -> dict | None:
        self.timeouts.append(timeout)
        if not self.reachable:
            raise Unreachable("no route to host")
        self.asked.append(email)
        return self.knows.get(email)


@pytest.fixture
def kiosk(tmp_path):
    """A kiosk on a port the OS chooses, torn down afterwards."""
    board = BoardCache(tmp_path / "board.json")
    roster = Roster(tmp_path / "known.json")
    server = Kiosk(board=board, roster=roster, client=FakeServer(),
                   outbox=Outbox(tmp_path / "outbox"), port=0)
    server.start()
    yield server
    server.stop()


def get(kiosk: Kiosk, path: str):
    with urllib.request.urlopen(kiosk.url.rstrip("/") + path, timeout=5) as reply:
        return reply.status, reply.read()


def post(kiosk: Kiosk, path: str, payload):
    request = urllib.request.Request(
        kiosk.url.rstrip("/") + path, method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as reply:
        return reply.status, json.loads(reply.read() or b"{}")


class TestThePage:
    def test_the_board_page_is_served(self, kiosk):
        status, body = get(kiosk, "/")
        assert status == 200
        assert b"Hand-Wheel Racer" in body

    def test_the_logo_is_served(self, kiosk):
        status, body = get(kiosk, "/assets/aisc.png")
        assert status == 200 and body[:4] == b"\x89PNG"

    def test_an_unknown_path_is_a_plain_404(self, kiosk):
        with pytest.raises(urllib.error.HTTPError) as raised:
            get(kiosk, "/wp-admin")
        assert raised.value.code == 404

    def test_it_only_listens_on_loopback(self, kiosk):
        """There is no authentication in this server because there is nothing
        that can reach it but this laptop. That has to stay true."""
        assert kiosk._server.server_address[0] == "127.0.0.1"


class TestTheBoard:
    def test_the_cached_board_is_what_the_page_gets(self, kiosk):
        kiosk.board.store(A_BOARD)
        status, body = get(kiosk, "/api/board")
        assert status == 200
        assert json.loads(body)["standings"][0]["name"] == "Marta"

    def test_an_empty_board_before_the_server_is_ever_reached(self, kiosk):
        """The first minutes of a fair, or a stand set up before the wifi
        works. The page already knows how to draw this."""
        status, body = get(kiosk, "/api/board")
        assert status == 200
        assert json.loads(body) == {"standings": [], "live": [],
                                    "players": 0, "runs": 0}

    def test_the_board_keeps_being_served_with_no_network(self, kiosk):
        """The reason this server exists at all."""
        kiosk.board.store(A_BOARD)
        kiosk.client.reachable = False
        _, body = get(kiosk, "/api/board")
        assert json.loads(body)["players"] == 9

    def test_the_board_is_never_cached_by_the_browser(self, kiosk):
        with urllib.request.urlopen(kiosk.url + "api/board", timeout=5) as reply:
            assert reply.headers["Cache-Control"] == "no-store"


class TestLookingSomebodyUp:
    def test_somebody_who_played_here_is_answered_from_disk(self, kiosk):
        kiosk.roster.remember("marta@example.com", "Marta Ruiz", 14.88)
        status, body = post(kiosk, "/booth/lookup", {"email": "marta@example.com"})
        assert status == 200
        assert body["best_seconds"] == 14.88
        assert body["source"] == "booth"

    def test_the_address_is_matched_the_way_the_server_matches_it(self, kiosk):
        kiosk.roster.remember("marta@example.com", "Marta", 14.88)
        _, body = post(kiosk, "/booth/lookup", {"email": "  MARTA@Example.COM "})
        assert body["best_seconds"] == 14.88

    def test_somebody_who_played_elsewhere_is_fetched(self, kiosk):
        """The case the whole rewrite exists for: a different laptop, at a
        different fair."""
        kiosk.client.knows["far@example.com"] = {"name": "Lejos",
                                                 "best_seconds": 12.0,
                                                 "runs": 3, "position": 1}
        _, body = post(kiosk, "/booth/lookup", {"email": "far@example.com"})
        assert body["best_seconds"] == 12.0
        assert body["source"] == "server"

    def test_the_game_is_never_left_waiting_on_the_default_timeout(self, kiosk):
        """Four seconds is fine for a queue draining in the background and far
        too long with somebody standing at the sign-in screen."""
        kiosk.client.knows["far@example.com"] = {"name": "Lejos",
                                                 "best_seconds": 12.0}
        post(kiosk, "/booth/lookup", {"email": "far@example.com"})
        assert kiosk.client.timeouts == [1.5]

    def test_a_fetched_player_is_remembered_for_next_time(self, kiosk):
        kiosk.client.knows["far@example.com"] = {"name": "Lejos",
                                                 "best_seconds": 12.0}
        post(kiosk, "/booth/lookup", {"email": "far@example.com"})
        kiosk.client.reachable = False
        _, body = post(kiosk, "/booth/lookup", {"email": "far@example.com"})
        assert body["best_seconds"] == 12.0

    def test_the_server_is_not_asked_about_somebody_already_known(self, kiosk):
        """Most sign-ins at a stand are somebody who played ten minutes ago.
        Asking the network about them would put a round trip in front of a
        person who has just pressed Enter."""
        kiosk.roster.remember("marta@example.com", "Marta", 14.88)
        post(kiosk, "/booth/lookup", {"email": "marta@example.com"})
        assert kiosk.client.asked == []

    def test_somebody_new_is_a_plain_404(self, kiosk):
        with pytest.raises(urllib.error.HTTPError) as raised:
            post(kiosk, "/booth/lookup", {"email": "new@example.com"})
        assert raised.value.code == 404

    def test_no_network_reads_as_somebody_new(self, kiosk):
        """A worse answer than the truth, and a much better one than a stall.
        The cost is a returning player not being shown their old time; the
        alternative is the sign-in screen hanging in front of them."""
        kiosk.client.reachable = False
        with pytest.raises(urllib.error.HTTPError) as raised:
            post(kiosk, "/booth/lookup", {"email": "far@example.com"})
        assert raised.value.code == 404

    def test_no_server_configured_reads_as_somebody_new(self, tmp_path):
        kiosk = Kiosk(board=BoardCache(tmp_path / "b.json"),
                      roster=Roster(tmp_path / "k.json"), client=None, port=0)
        kiosk.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as raised:
                post(kiosk, "/booth/lookup", {"email": "anyone@example.com"})
            assert raised.value.code == 404
        finally:
            kiosk.stop()

    def test_a_request_with_no_address_is_rejected(self, kiosk):
        with pytest.raises(urllib.error.HTTPError) as raised:
            post(kiosk, "/booth/lookup", {"nombre": "Marta"})
        assert raised.value.code == 400

    def test_lookup_is_the_only_thing_that_can_be_posted(self, kiosk):
        with pytest.raises(urllib.error.HTTPError) as raised:
            post(kiosk, "/api/board", {})
        assert raised.value.code == 404


class TestStatus:
    def test_status_says_what_is_queued(self, kiosk, tmp_path):
        """Read by whoever is setting the stand up, to tell 'nobody has played
        yet' from 'nothing is getting through'."""
        from wheel_racer.station.outbox import PendingRun

        kiosk.outbox.add(PendingRun(
            name="Marta", email="marta@example.com", seconds=14.88,
            station="booth-1", terms_version="2026-01",
            terms_accepted_at="2026-03-14T10:00:00+00:00"))
        _, body = get(kiosk, "/booth/status")
        assert json.loads(body)["queued"] == 1

    def test_status_says_how_old_the_board_is(self, kiosk):
        _, body = get(kiosk, "/booth/status")
        assert json.loads(body)["board_age"] is None
        kiosk.board.store(A_BOARD)
        _, body = get(kiosk, "/booth/status")
        assert json.loads(body)["board_age"] is not None
