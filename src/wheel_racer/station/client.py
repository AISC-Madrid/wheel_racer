"""Talking to the server, over whatever wifi the venue has.

`urllib` from the standard library rather than `requests` or `httpx`. The game
already installs numpy, pygame and — on the machine that matters — MediaPipe,
onto a laptop that has to be set up in a car park before a fair; adding a
dependency to make four HTTP calls is not a trade worth making. Nothing here
needs connection pooling, and everything here needs to work on a fresh
checkout.

The one idea that matters is the distinction between **unreachable** and
**refused**:

  * Unreachable is the wifi, and the answer is to try again later. Nothing is
    lost, nothing is logged loudly, and the queue keeps its place.
  * Refused is the server saying this will never work — a malformed run, a lap
    time nobody drove, a body it cannot parse. Retrying that forever would
    wedge the queue behind one bad entry and every result after it would stop
    reaching the board.

Getting those two the wrong way round is how a booth ends up with either a lost
afternoon or a stuck one, so they are separate exception types and every caller
has to answer for both.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

# Short on purpose. Everything this client does happens while somebody is
# standing at a machine, and a slow answer is worth no more than no answer:
# the queue will come back to it in a few seconds either way.
DEFAULT_TIMEOUT = 4.0


class Unreachable(Exception):
    """The server could not be spoken to. Try again later."""


class Refused(Exception):
    """The server understood and said no. Trying again will not help."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Client:
    """The server, as four methods.

    Holds no connection and no state, so it is safe to build one per call and
    safe to share one between threads.
    """

    base_url: str
    token: str
    timeout: float = DEFAULT_TIMEOUT

    # --- the booth's half ----------------------------------------------------

    def submit(self, run: dict) -> dict:
        """File one finished run. Safe to call again with the same id."""
        return self._call("POST", "/api/runs", run) or {}

    def lookup(self, email: str) -> dict | None:
        """What this address has done before, or None if it is somebody new."""
        try:
            return self._call("POST", "/api/players/lookup", {"email": email})
        except Refused as refusal:
            # Never having played is an ordinary answer, not a problem: it is
            # what happens every time somebody new walks up to the stand.
            if refusal.status == 404:
                return None
            raise

    def station(self, update: dict) -> None:
        """Say what this booth is doing. Fire and forget."""
        self._call("POST", "/api/stations", update)

    def closing(self, station: str) -> None:
        """Packing up — take this stand off the live column now."""
        self._call("DELETE", f"/api/stations/{station}", None)

    # --- reading -------------------------------------------------------------

    def board(self, limit: int | None = None) -> dict:
        """The public board, for the kiosk to be served from a local copy."""
        query = f"?limit={int(limit)}" if limit else ""
        return self._call("GET", f"/api/board{query}", None) or {}

    # --- the wire ------------------------------------------------------------

    def _call(self, method: str, path: str, payload: dict | None) -> dict | None:
        request = urllib.request.Request(
            self.base_url.rstrip("/") + path,
            method=method,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                # Named so a server log line can be traced back to a booth
                # laptop rather than to "python-urllib".
                "User-Agent": "hand-wheel-racer-booth",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            raise _classify(error) from error
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            # No route, no DNS, no server listening, TLS refused, timed out.
            # All of them are "the wifi", and all of them are worth retrying.
            raise Unreachable(str(error)) from error
        except ValueError as error:
            # A 200 whose body is not JSON. Something is answering that is not
            # our server — a captive portal at a conference is the usual one,
            # and it will stop being there when somebody logs in.
            raise Unreachable(f"unreadable reply: {error}") from error


def _classify(error: urllib.error.HTTPError) -> Exception:
    """Decide whether an HTTP error is worth retrying.

    The two that look permanent but are not:

      * **401** — a token that is wrong now may be right in a minute, because
        the fix is somebody putting the right one in the environment and
        restarting. Treating it as permanent would throw away the queue over a
        typo.
      * **429** — rate limited, which is the server asking for a pause and not
        for a surrender.
    """
    if error.code in (401, 408, 429) or error.code >= 500:
        return Unreachable(f"HTTP {error.code}")

    try:
        detail = json.loads(error.read()).get("detail", "")
    except (ValueError, OSError):
        detail = error.reason or ""
    return Refused(error.code, str(detail))
