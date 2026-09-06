"""The kiosk's web server, on the laptop, on loopback.

The screen behind the stand is a browser in fullscreen. Pointing it straight at
the VPS would be simpler by exactly one file — and would mean that the moment
the venue's wifi drops, the biggest thing at the stand goes blank in front of a
queue. So the browser is pointed here instead, and this serves the same page
from a copy of the last board the server gave us.

It answers three kinds of request:

  * the page and the logo, which are the same files the server ships;
  * `/api/board`, from the cache, so the page cannot tell the difference;
  * `/booth/lookup`, which is the game asking what a player's record is.

It binds to loopback and nothing else. There is no authentication anywhere in
here, because there is nothing to authenticate: the only thing that can reach
it is a process on this laptop.

`http.server` rather than the framework the VPS runs on. This has to start on a
booth laptop that has pygame and MediaPipe installed and nothing else, and four
routes over the standard library is a fair price for the game's dependency list
not growing a web stack.
"""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .cache import BoardCache, Roster
from .client import Client, Refused, Unreachable

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "web"
ASSETS = REPO / "assets"

# What the page gets before the server has ever been reached: a board with
# nothing on it, which the page already knows how to draw as "nobody has
# finished a lap yet".
EMPTY_BOARD = {"standings": [], "live": [], "players": 0, "runs": 0}

TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png",
         ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".ico": "image/x-icon"}

# How long the game is prepared to wait for an answer about a returning player.
# It is spent in front of somebody who has just pressed Enter, so it is short:
# a missing personal best costs them a line on a screen, and a stall costs the
# stand its momentum.
LOOKUP_TIMEOUT = 1.5


class Handler(BaseHTTPRequestHandler):
    """One request. Holds nothing; everything comes from the station."""

    protocol_version = "HTTP/1.1"

    def __init__(self, station, *args, **kwargs) -> None:
        self.station = station
        super().__init__(*args, **kwargs)

    # --- routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            self._file(WEB / "index.html")
        elif path == "/api/board":
            self._json(self.station.board.latest() or EMPTY_BOARD)
        elif path == "/booth/status":
            self._json(self.station.status())
        elif path.startswith("/assets/"):
            self._file(ASSETS / Path(path).name)
        else:
            self._json({"detail": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/booth/lookup":
            self._json({"detail": "not found"}, status=404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
            email = str(request["email"])
        except (ValueError, KeyError, TypeError):
            self._json({"detail": "expected {\"email\": ...}"}, status=400)
            return

        found = self.station.lookup(email)
        if found is None:
            self._json({"detail": "never played"}, status=404)
        else:
            self._json(found)

    # --- replies -------------------------------------------------------------

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The board changes every few seconds and the page asks for it on a
        # timer; a cached copy would freeze the kiosk on whatever it saw first.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._json({"detail": "not found"}, status=404)
            return

        self.send_response(200)
        self.send_header("Content-Type",
                         TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """Silence.

        The default writes a line per request to stderr, and the kiosk asks for
        the board every three seconds for eight hours. The one console the
        booth has is for things somebody needs to read.
        """


class Kiosk:
    """The local server, and the little bit of state it answers from."""

    def __init__(self, board: BoardCache, roster: Roster,
                 client: Client | None, outbox=None, port: int = 8752) -> None:
        self.board = board
        self.roster = roster
        self.client = client
        self.outbox = outbox
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # --- what the handler asks for -------------------------------------------

    def lookup(self, email: str) -> dict | None:
        """What is known about an address, local knowledge first.

        The roster is checked before the network on purpose. It is instant, it
        is right for everybody who has played at this laptop today — which is
        most sign-ins at a stand — and it means a returning player is greeted
        with their record even when the wifi is gone.

        The server is asked only when this machine has never seen the address.
        That is the case the whole rewrite exists for: somebody who drove at a
        different stand, on a different laptop, at a different fair.
        """
        key = email.strip().lower()
        known = self.roster.get(key)
        if known is not None:
            return {"name": known.get("name", ""),
                    "best_seconds": known.get("best_seconds"),
                    "source": "booth"}

        if self.client is None:
            return None
        try:
            # A shorter timeout than the client's usual one. The game is
            # blocked behind this call with somebody standing at the machine,
            # and a slow answer is worth no more to them than no answer.
            found = self.client.lookup(key, timeout=LOOKUP_TIMEOUT)
        except (Unreachable, Refused):
            return None
        if found is None:
            return None

        self.roster.remember(key, name=found.get("name", ""),
                             best_seconds=found.get("best_seconds"))
        return {**found, "source": "server"}

    def status(self) -> dict:
        """Enough for whoever is setting the stand up to see it is working."""
        return {
            "queued": self.outbox.count() if self.outbox is not None else 0,
            "board_age": self.board.age(),
            "known_players": len(self.roster),
            "server": self.client.base_url if self.client else None,
        }

    # --- running -------------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> str:
        """Open the port on a background thread. Returns the kiosk's URL.

        Threaded rather than forked, and a daemon, so that closing the game
        takes this with it — a booth laptop must never be left with an
        orphaned server holding a port that the next launch needs.
        """
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port),
                                           partial(Handler, self))
        # Port 0 means "any free one", which is how the tests get a server
        # without picking a number and hoping. Reading it back afterwards keeps
        # `url` honest in both cases.
        self.port = self._server.server_address[1]
        # A tenth of a second rather than the default half, because this is
        # what `stop` waits for: closing the game should not spend half a
        # second on a server that has nothing left to do.
        self._thread = threading.Thread(
            target=self._server.serve_forever, args=(0.1,),
            name="kiosk", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
