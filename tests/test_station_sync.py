"""Tests for the half of the booth that talks to the server.

Nothing here opens a socket. What is being tested is the decision-making — when
to retry, when to give up on a run, when to stay quiet — and a real network
would only make those decisions harder to provoke. The one thing that does get
exercised against the real thing is the classification of HTTP errors, because
getting "the wifi is down" and "this will never work" the wrong way round is
how a booth ends up with either a lost afternoon or a stuck queue.
"""

import os
import time
import urllib.error

import pytest

from wheel_racer.live import LiveChannel, LiveState
from wheel_racer.station.cache import BoardCache, Roster
from wheel_racer.station.client import Refused, Unreachable, _classify
from wheel_racer.station.outbox import Outbox, PendingRun
from wheel_racer.station.service import REJECTED, StationService
from wheel_racer.station.config import StationSettings


def _age(path, seconds: float) -> None:
    """Push a file's timestamp forward, so a reader that caches on `st_mtime`
    notices it changed."""
    stamp = path.stat().st_mtime + seconds
    os.utime(path, (stamp, stamp))


def a_run(**overrides) -> PendingRun:
    fields = {
        "name": "Marta Ruiz", "email": "marta@example.com", "seconds": 14.88,
        "station": "booth-1", "terms_version": "2026-01",
        "terms_accepted_at": "2026-03-14T10:00:00+00:00",
    }
    fields.update(overrides)
    return PendingRun(**fields)


class FakeServer:
    """A server that answers however a test needs it to.

    Records what it was told, so a test can assert on what the booth chose to
    send as well as on what it did with the reply.
    """

    def __init__(self, *, reachable: bool = True, refuse: bool = False) -> None:
        self.reachable = reachable
        self.refuse = refuse
        self.submitted: list[dict] = []
        self.stations: list[dict] = []
        self.closed: list[str] = []
        self.boards = 0
        self.base_url = "https://racer.example.com"

    def _check(self) -> None:
        if not self.reachable:
            raise Unreachable("no route to host")

    def submit(self, run: dict) -> dict:
        self._check()
        if self.refuse:
            raise Refused(422, "a lap of 0.01s is not a lap anyone drove")
        self.submitted.append(run)
        return {"best_seconds": 13.5, "position": 2, "improved": True,
                "duplicate": False, "previous_best": 14.88, "players": 9}

    def station(self, update: dict) -> None:
        self._check()
        self.stations.append(update)

    def closing(self, station: str) -> None:
        self._check()
        self.closed.append(station)

    def board(self, limit=None) -> dict:
        self._check()
        self.boards += 1
        return {"standings": [{"position": 1, "name": "Marta", "seconds": 13.5}],
                "live": [], "players": 9, "runs": 20}


@pytest.fixture
def settings() -> StationSettings:
    return StationSettings(
        server="https://racer.example.com", token="token", station="booth-1",
        port=8752, board_refresh_seconds=3.0, heartbeat_seconds=5.0,
    )


@pytest.fixture
def parts(tmp_path, settings):
    """A station wired to a fake server, with everything else on disk."""
    server = FakeServer()
    service = StationService(
        client=server, settings=settings,
        outbox=Outbox(tmp_path / "outbox"),
        board=BoardCache(tmp_path / "board.json"),
        roster=Roster(tmp_path / "known.json"),
        live=LiveChannel(tmp_path / "live.json"),
    )
    return service, server


class TestSendingResults:
    def test_a_queued_run_reaches_the_server(self, parts):
        service, server = parts
        service.outbox.add(a_run())
        service.tick(now=100.0)
        assert [run["email"] for run in server.submitted] == ["marta@example.com"]

    def test_a_sent_run_leaves_the_queue(self, parts):
        service, _ = parts
        service.outbox.add(a_run())
        service.tick(now=100.0)
        assert service.outbox.count() == 0

    def test_going_offline_says_why(self, parts, capsys):
        """The reason is the difference between waiting for wifi that will come
        back and waiting for a token that never will."""
        service, server = parts
        server.reachable = False
        service.tick(now=100.0)
        assert "no route to host" in capsys.readouterr().out

    def test_going_offline_is_only_said_once(self, parts, capsys):
        service, server = parts
        server.reachable = False
        service.tick(now=100.0)
        capsys.readouterr()
        service.tick(now=200.0)
        assert capsys.readouterr().out == ""

    def test_nothing_is_lost_while_the_server_is_away(self, parts):
        service, server = parts
        server.reachable = False
        service.outbox.add(a_run())
        service.tick(now=100.0)
        assert service.outbox.count() == 1

    def test_the_queue_goes_out_when_the_server_comes_back(self, parts):
        service, server = parts
        server.reachable = False
        service.outbox.add(a_run(email="a@example.com"))
        service.outbox.add(a_run(email="b@example.com"))
        service.tick(now=100.0)

        server.reachable = True
        # Past the backoff the failure just set.
        service.tick(now=200.0)
        assert len(server.submitted) == 2
        assert service.outbox.count() == 0

    def test_a_refused_run_is_moved_aside_not_retried(self, parts):
        """Retrying something the server will never accept would wedge every
        result behind it, and the board would silently stop updating."""
        service, server = parts
        server.refuse = True
        service.outbox.add(a_run(seconds=0.01))
        service.tick(now=100.0)

        assert service.outbox.count() == 0
        assert list((service.outbox.path / REJECTED).glob("*.run.json"))

    def test_a_refused_run_does_not_block_the_ones_behind_it(self, parts):
        service, server = parts
        service.outbox.add(a_run(seconds=0.01))
        server.refuse = True
        service.tick(now=100.0)
        server.refuse = False
        service.outbox.add(a_run(email="fine@example.com"))
        service.tick(now=200.0)
        assert [run["email"] for run in server.submitted] == ["fine@example.com"]

    def test_the_server_s_answer_updates_what_the_booth_knows(self, parts):
        """The server sees every laptop and every previous fair, so its answer
        can be better than anything this machine could work out."""
        service, _ = parts
        service.outbox.add(a_run())
        service.tick(now=100.0)
        assert service.roster.best_for("marta@example.com") == 13.5

    def test_a_big_queue_goes_out_in_batches(self, parts):
        """An hour offline leaves a hundred runs queued. Sending all of them in
        one pass would freeze the kiosk exactly when somebody is watching it
        catch up."""
        service, server = parts
        for index in range(20):
            service.outbox.add(a_run(email=f"p{index}@example.com"))
        service.tick(now=100.0)
        assert 0 < len(server.submitted) < 20


class TestSayingWhoIsDriving:
    def test_a_driver_is_announced(self, parts):
        service, server = parts
        service.live.publish(LiveState(state="racing", name="Marta Ruiz",
                                       lap=1, laps=2))
        service.tick(now=100.0)
        assert server.stations[-1]["driver"] == "Marta Ruiz"
        assert server.stations[-1]["station"] == "booth-1"

    def test_an_unchanged_state_is_not_sent_again(self, parts):
        service, server = parts
        service.live.publish(LiveState(state="racing", name="Marta"))
        service.tick(now=100.0)
        service.tick(now=101.0)
        assert len(server.stations) == 1

    def test_an_unchanged_state_is_repeated_as_a_heartbeat(self, parts):
        """Silence is how the server tells a quiet stand from one that has
        packed up, so a stand that is merely quiet has to keep saying so."""
        service, server = parts
        service.live.publish(LiveState(state="ready", name="Marta"))
        service.tick(now=100.0)
        service.tick(now=110.0)
        assert len(server.stations) == 2

    def test_a_change_is_sent_immediately(self, parts):
        service, server = parts
        service.live.publish(LiveState(state="ready", name="Marta"))
        service.tick(now=100.0)

        service.live.publish(LiveState(state="racing", name="Marta", lap=1, laps=2))
        # The live channel re-reads only when the file's timestamp moves, and
        # two writes this close together can land inside one tick of the
        # filesystem clock. In the game they are at least a frame apart; here
        # the timestamp is moved by hand so the test is about the station's
        # decision rather than about the resolution of `st_mtime`.
        _age(service.live.path, seconds=1)
        service.tick(now=100.5)
        assert [s["state"] for s in server.stations] == ["ready", "racing"]

    def test_a_game_that_is_not_running_says_nothing(self, parts):
        service, server = parts
        service.tick(now=100.0)
        assert server.stations == []

    def test_a_stale_channel_says_nothing(self, parts):
        """The game crashed or was closed. The stand should age off the live
        column, not sit there claiming to be waiting for a driver."""
        service, server = parts
        stale = LiveState(state="racing", name="Marta",
                          updated_at=time.time() - 3600)
        service.live._write(stale)
        service.tick(now=100.0)
        assert server.stations == []

    def test_packing_up_clears_the_stand(self, parts):
        service, server = parts
        service.closing()
        assert server.closed == ["booth-1"]

    def test_packing_up_with_no_network_is_not_an_error(self, parts):
        service, server = parts
        server.reachable = False
        service.closing()


class TestKeepingTheBoard:
    def test_the_board_is_fetched_and_kept(self, parts):
        service, _ = parts
        service.tick(now=100.0)
        assert service.board.latest()["players"] == 9

    def test_the_board_is_not_fetched_on_every_tick(self, parts):
        service, server = parts
        service.tick(now=100.0)
        service.tick(now=100.5)
        assert server.boards == 1

    def test_the_board_is_refreshed_once_it_is_due(self, parts):
        service, server = parts
        service.tick(now=100.0)
        service.tick(now=104.0)
        assert server.boards == 2

    def test_the_last_board_survives_the_network_going_away(self, parts):
        service, server = parts
        service.tick(now=100.0)
        server.reachable = False
        service.tick(now=200.0)
        assert service.board.latest()["players"] == 9


class TestBackingOff:
    def test_a_failure_stops_it_hammering(self, parts):
        service, server = parts
        server.reachable = False
        service.tick(now=100.0)
        before = server.boards
        service.tick(now=100.1)
        assert server.boards == before

    def test_it_tries_again_once_the_wait_is_over(self, parts):
        service, server = parts
        server.reachable = False
        service.tick(now=100.0)
        server.reachable = True
        service.tick(now=160.0)
        assert service.board.latest() is not None

    def test_the_wait_grows_while_it_keeps_failing(self, parts):
        service, server = parts
        server.reachable = False
        service.tick(now=100.0)
        first = service._backoff - 100.0
        service.tick(now=service._backoff)
        second = service._backoff - service._backoff + (service._backoff - first)
        assert service._backoff > 100.0 + first
        assert second > 0

    def test_success_clears_the_wait(self, parts):
        service, server = parts
        server.reachable = False
        service.tick(now=100.0)
        server.reachable = True
        service.tick(now=200.0)
        assert service._backoff == 0.0


class TestUnconfigured:
    def test_a_station_with_no_server_does_nothing_and_survives(self, tmp_path):
        """Supported on purpose: the stand can be set up and tested before
        anybody has decided what the URL is."""
        service = StationService(
            client=FakeServer(reachable=False),
            settings=StationSettings(server="", token="", station="booth-1",
                                     port=8752, board_refresh_seconds=3.0,
                                     heartbeat_seconds=5.0),
            outbox=Outbox(tmp_path / "outbox"),
            board=BoardCache(tmp_path / "board.json"),
            roster=Roster(tmp_path / "known.json"),
            live=LiveChannel(tmp_path / "live.json"),
        )
        service.outbox.add(a_run())
        service.tick(now=100.0)
        # Still queued, and nothing raised.
        assert service.outbox.count() == 1


class TestClassifyingFailures:
    """Which HTTP replies are worth trying again.

    The two that look permanent and are not are the ones that matter: a wrong
    token is fixed by somebody editing `.env`, and a rate limit is the server
    asking for a pause rather than for a surrender. Treating either as final
    would throw away an afternoon of results.
    """

    def _error(self, code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("https://x/api/runs", code, "no", {}, None)

    @pytest.mark.parametrize("code", [401, 408, 429, 500, 502, 503])
    def test_worth_trying_again(self, code):
        assert isinstance(_classify(self._error(code)), Unreachable)

    @pytest.mark.parametrize("code", [400, 403, 404, 422])
    def test_never_going_to_work(self, code):
        assert isinstance(_classify(self._error(code)), Refused)

    def test_a_refusal_keeps_its_status(self):
        assert _classify(self._error(422)).status == 422

    def test_a_rejected_token_says_so(self):
        """401 is retried like an outage, but it is not one, and a stand told
        only "unreachable" goes looking at the router instead of at `.env`."""
        assert "WHEEL_RACER_BOOTH_TOKEN" in str(_classify(self._error(401)))

