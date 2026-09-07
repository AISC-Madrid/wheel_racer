"""Where a finished run goes, and where a returning player's record comes from.

This replaces the CSV. The reason is not that a CSV was too slow — at booth
sizes it was instant — but that it was tied to one laptop. A player who drove
at last term's fair got nothing back, the file had to be collected from
whoever's machine ran the stand, and swapping laptops meant starting the
leaderboard again.

The shape of the replacement is deliberately lopsided:

  * **Saving never touches the network.** `record` writes one small file to the
    outbox and returns. It cannot fail for any reason a person at the wheel
    would recognise, and it cannot block the frame after the finish line.
  * **Asking may.** `best_for` answers from this laptop's own roster when it
    can — which it can for anybody who has played here today — and only calls
    the station on loopback for a face it has never seen. If nothing answers
    in time, the game is told "no record", which costs somebody one line on a
    screen and is far better than a sign-in that hangs in front of a queue.

That asymmetry is the whole design: the thing that must not be lost is written
to disk immediately, and the thing that is merely nice to know is allowed to be
missing.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from . import config
from .station.cache import Roster
from .station.config import settings
from .station.outbox import Outbox, PendingRun

# Times are kept to the hundredth, which is what the clock on screen shows and
# what the server stores. Rounding on the way in rather than on the way out
# keeps the value in memory identical to the value everywhere else — otherwise
# the same run counts as a personal best here and not there.
PRECISION = 2

# How long the sign-in screen will wait for an answer about somebody.
#
# Longer than the station's own lookup timeout on purpose, so that when the
# station does go to the server, its answer gets back here rather than being
# abandoned a moment before it arrives. Everything past this is spent in front
# of a person who has just pressed Enter.
LOOKUP_TIMEOUT = 2.5

# How long to leave a silent station alone before asking it anything again.
# About the length of a run, so a station that comes back is noticed by the
# person after next rather than by nobody.
STATION_SILENCE = 45.0


@dataclass(frozen=True)
class Player:
    """What is known about the person at the wheel."""

    name: str
    email: str
    best_seconds: float
    """Their quickest single lap, in seconds — not the total for a run."""


def normalise_email(email: str) -> str:
    """The form an address is compared in.

    Case and stray spaces are how the same person ends up counted twice, and at
    a booth people type their address in a hurry. The server applies the same
    rule; the two have to agree, or a returning player stops being recognised
    as themselves.
    """
    return email.strip().lower()


class Results:
    """The booth's record of the afternoon, as the game asks about it."""

    def __init__(self, outbox: Outbox | None = None, roster: Roster | None = None,
                 kiosk_port: int | None = None, station: str | None = None) -> None:
        """`kiosk_port` of 0 means there is no station to ask at all.

        Not the same as one that is down: a port nothing is listening on has to
        be tried before that is known, and on Windows finding out costs about
        two seconds. Zero is how the game says "do not bother" — which is what
        `--no-station` means, and what most tests want.
        """
        self.outbox = outbox if outbox is not None else Outbox()
        self.roster = roster if roster is not None else Roster()
        booth = settings()
        self.kiosk_port = kiosk_port if kiosk_port is not None else booth.port
        self.station = station if station is not None else booth.station
        self._silent_until = 0.0
        """When it is worth talking to the station again, on the monotonic clock."""

    # --- asking --------------------------------------------------------------

    def best_for(self, email: str) -> float | None:
        """The quickest lap this address has ever set, as far as anyone knows.

        Local knowledge first. Most sign-ins at a stand are somebody who played
        twenty minutes ago, and for them the answer is already on this disk —
        so the common case costs a dictionary lookup and the station is only
        troubled about a face this laptop has genuinely never seen.

        Asked once, at sign-in, and never again during a run: the answer is
        kept by the game and updated from what `record` returns, so nothing on
        the finish line waits for anything.
        """
        key = normalise_email(email)
        known = self.roster.best_for(key)
        if known is not None:
            return known

        found = self._ask_station(key)
        if found is None:
            return None
        best = found.get("best_seconds")
        return float(best) if isinstance(best, (int, float)) else None

    def _ask_station(self, email_key: str) -> dict | None:
        """Ask the local station about somebody. None if it cannot say.

        Every failure here is the same failure — no station, no answer, a reply
        that is not JSON — and all of them mean "carry on without it".

        A station that did not answer is left alone for a while afterwards.
        Refusing a connection is not free: on Windows a closed loopback port
        takes about two seconds to say so, and without this every single
        sign-in would freeze the game for that long while the station was down.
        One person pays it, once a minute, instead of everybody.
        """
        if not self.kiosk_port or time.monotonic() < self._silent_until:
            return None

        request = urllib.request.Request(
            f"http://127.0.0.1:{self.kiosk_port}/booth/lookup",
            method="POST",
            data=json.dumps({"email": email_key}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=LOOKUP_TIMEOUT) as reply:
                return json.loads(reply.read() or b"{}")
        except urllib.error.HTTPError:
            # The station answered — with "never played", most likely. It is
            # up, so there is no reason to stop asking it.
            return None
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            self._silent_until = time.monotonic() + STATION_SILENCE
            return None

    # --- saving --------------------------------------------------------------

    def record(self, name: str, email: str, seconds: float,
               accepted_at: datetime | None = None) -> Player:
        """Save a run. Returns what this laptop now believes their best is.

        Writes to disk and returns; the station sends it on when it can. The
        value that comes back is the better of this run and whatever was
        already known here, which is what the celebration screen needs and is
        the same answer the server will settle on.
        """
        key = normalise_email(email)
        seconds = round(seconds, PRECISION)
        stamp = accepted_at or datetime.now(timezone.utc)

        self.outbox.add(PendingRun(
            name=name.strip(),
            email=key,
            seconds=seconds,
            station=self.station,
            terms_version=config.TERMS_VERSION,
            terms_accepted_at=stamp.isoformat(),
        ))

        # Recorded here as well as queued, so a second run five minutes later
        # is measured against this one even if the wifi has not come back yet.
        self.roster.remember(key, name=name.strip(), best_seconds=seconds)
        best = self.roster.best_for(key)
        return Player(name=name.strip(), email=key,
                      best_seconds=best if best is not None else seconds)

    # --- for the stand -------------------------------------------------------

    @property
    def queued(self) -> int:
        """Runs written but not yet sent. Shown while setting up, so somebody
        can tell "nobody has played" from "nothing is getting through"."""
        return self.outbox.count()
