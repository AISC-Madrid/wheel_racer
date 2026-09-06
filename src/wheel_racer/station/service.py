"""The part that talks to the server, so that nothing else has to.

One loop, three jobs, in the order they matter at a stand:

  1. **Empty the outbox.** Somebody drove that. It is the only thing here that
     cannot be reconstructed if it is lost.
  2. **Say who is driving.** Read out of the live channel the game already
     writes, so the game needs no knowledge that any of this exists.
  3. **Fetch the board.** So the kiosk screen has something to draw, including
     during the twenty minutes the wifi is away.

Everything it does is allowed to fail. A booth afternoon has exactly one
unacceptable outcome — the person at the wheel finding out — and every failure
below is caught and turned into "try again in a moment".

The loop deliberately holds no lock and shares nothing with the local HTTP
server beyond two cache objects, each of which is replaced wholesale rather
than mutated. That is enough: CPython guarantees the assignment is atomic, and
the worst case is a screen drawing the previous board for one more frame.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..live import LiveChannel
from .cache import BoardCache, Roster
from .client import Client, Refused, Unreachable
from .config import StationSettings
from .outbox import Outbox

# Where a run goes when the server says it will never accept it. Kept rather
# than deleted: it is the only evidence of a result that somebody drove and the
# board will never show, and at a stand that is a thing worth being able to
# look at afterwards.
REJECTED = "rejected"

# How far the retry interval backs off while the network is away. Long enough
# to stop a laptop with no wifi from spending its afternoon on DNS lookups,
# short enough that the board catches up within a few seconds of the signal
# coming back.
BACKOFF_START = 2.0
BACKOFF_MAX = 30.0

# Runs are sent one at a time, and this caps how many go in a single pass. An
# outage that lasted an hour leaves a hundred queued, and pushing all of them
# before drawing the next frame would freeze the kiosk screen at the exact
# moment the wifi came back and somebody was watching.
BATCH = 8


@dataclass
class StationService:
    """The booth's link to the server. Owns no screen and blocks nobody."""

    client: Client
    settings: StationSettings
    outbox: Outbox
    board: BoardCache
    roster: Roster
    live: LiveChannel

    def __post_init__(self) -> None:
        self._last_station: dict | None = None
        self._station_sent_at = 0.0
        self._board_fetched_at = 0.0
        self._backoff = 0.0
        """When it is worth trying the network again, on the monotonic clock."""
        self._delay = 0.0
        """How long the current wait is, doubling for as long as it fails."""
        self._offline_since: float | None = None

    # --- one pass ------------------------------------------------------------

    def tick(self, now: float | None = None) -> None:
        """Do whatever is due. Never raises."""
        moment = time.monotonic() if now is None else now
        if not self.settings.configured or moment < self._backoff:
            return

        try:
            self._drain()
            self._announce(moment)
            self._refresh(moment)
        except Unreachable as outage:
            # Usually the venue's wifi: not news, not an error, and not worth a
            # line of output every two seconds for the rest of the afternoon.
            # Said once, with the reason, because "unreachable" also covers a
            # rejected token and a laptop with no certificates, and those two
            # do not fix themselves by waiting.
            self._go_offline(moment, outage)
        else:
            self._came_back(moment)

    def _drain(self) -> None:
        """Send queued runs, oldest first, until one cannot go."""
        for file, payload in self.outbox.pending()[:BATCH]:
            try:
                receipt = self.client.submit(payload)
            except Refused as refusal:
                # The server will never take this. Retrying it forever would
                # wedge every result behind it, so it is moved aside and the
                # queue carries on.
                self._reject(file, refusal)
                continue

            self.outbox.done(file)
            self._remember(payload, receipt)

    def _remember(self, payload: dict, receipt: dict) -> None:
        """Take the server's word for what this player's best is now.

        The server sees every laptop and every previous fair, so its answer can
        be better than anything known here — which is the entire reason the CSV
        was worth replacing.
        """
        email = str(payload.get("email", "")).strip().lower()
        if not email:
            return
        self.roster.remember(
            email,
            name=str(payload.get("name", "")),
            best_seconds=receipt.get("best_seconds"),
            position=receipt.get("position"),
        )

    def _reject(self, file: Path, refusal: Refused) -> None:
        rejected = self.outbox.path / REJECTED
        rejected.mkdir(parents=True, exist_ok=True)
        try:
            file.replace(rejected / file.name)
        except OSError:
            self.outbox.done(file)
        print(f"station: server refused a run ({refusal}) — moved to {rejected}")

    def _announce(self, now: float) -> None:
        """Tell the server who is at the wheel, if that has changed."""
        state = self.live.read()
        if state is None or state.is_stale():
            # The game is not running. Say nothing rather than sending "idle":
            # the server ages a silent station off the live column on its own,
            # and a stand that has genuinely packed up should disappear rather
            # than sit there claiming to be waiting for a driver.
            self._last_station = None
            return

        update = {
            "station": self.settings.station,
            "state": state.state,
            "driver": state.name,
            "lap": state.lap,
            "laps": state.laps,
        }
        due = now - self._station_sent_at >= self.settings.heartbeat_seconds
        if update == self._last_station and not due:
            return

        self.client.station(update)
        self._last_station = update
        self._station_sent_at = now

    def _refresh(self, now: float) -> None:
        if now - self._board_fetched_at < self.settings.board_refresh_seconds:
            return
        self.board.store(self.client.board(), now)
        self._board_fetched_at = now

    # --- being offline -------------------------------------------------------

    def _go_offline(self, now: float, reason: Exception | None = None) -> None:
        if self._offline_since is None:
            self._offline_since = now
            because = f" ({reason})" if reason else ""
            print(f"station: server unreachable{because} — "
                  "queueing until it comes back")
        self._delay = min(max(BACKOFF_START, self._delay * 2), BACKOFF_MAX)
        self._backoff = now + self._delay

    def _came_back(self, now: float) -> None:
        if self._offline_since is not None:
            away = now - self._offline_since
            print(f"station: server back after {away:.0f}s — "
                  f"{self.outbox.count()} run(s) still queued")
            self._offline_since = None
        self._backoff = 0.0
        self._delay = 0.0

    # --- shutting down -------------------------------------------------------

    def closing(self) -> None:
        """Take this stand off the live column, on the way out.

        Best effort and quick: this runs while somebody is closing a laptop,
        and the server ages the station off by itself twenty seconds later
        anyway.
        """
        if not self.settings.configured:
            return
        try:
            self.client.closing(self.settings.station)
        except (Unreachable, Refused):
            pass
