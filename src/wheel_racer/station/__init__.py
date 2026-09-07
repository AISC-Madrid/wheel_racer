"""The booth's link to the outside world.

The game plays. The server holds the results. This is the piece in between, and
it exists so that neither of the other two has to know about the network:

    game ──writes──> data/outbox/   ──sends──> station ──HTTPS──> server
    game ──writes──> data/live.json ──reads──>    │
                                                  │
    kiosk browser ──http://127.0.0.1──────────────┘

Everything crosses that boundary as a file or as a request to loopback, and
neither can hang, time out or fail in a way the person at the wheel finds out
about. The game writes a result to disk in a microsecond and draws the next
frame; whether that result is on a screen in another country three seconds
later or twenty minutes later is this module's problem, and nobody else's.

Run it alongside the game — `run.py` starts it — or on its own while working on
the board:

    python station.py
"""

from __future__ import annotations

import signal
import time
import webbrowser

from ..live import LiveChannel
from .cache import BoardCache, Roster
from .client import Client
from .config import settings
from .outbox import Outbox
from .server import Kiosk
from .service import StationService

# How often the loop wakes up. Nothing here is due more often than every couple
# of seconds, but a short tick is what makes a result reach the board quickly
# after it is queued, and it costs one comparison when there is nothing to do.
TICK_SECONDS = 0.5


def build(open_kiosk: bool = False) -> tuple[StationService, Kiosk]:
    """Assemble the station from the environment.

    Returned rather than run, so tests and `run.py` can hold the pieces.
    """
    config = settings()
    client = Client(config.server, config.token) if config.configured else None

    outbox = Outbox()
    board = BoardCache()
    roster = Roster()

    kiosk = Kiosk(board=board, roster=roster, client=client,
                  outbox=outbox, port=config.port)
    service = StationService(
        # A service with no client is still worth having: it keeps the kiosk
        # served and the queue growing safely on disk until somebody puts a URL
        # in `.env` and restarts.
        client=client or Client("", ""),
        settings=config, outbox=outbox, board=board,
        roster=roster, live=LiveChannel(),
    )

    kiosk.start()
    if open_kiosk:
        webbrowser.open(kiosk.url)
    return service, kiosk


def run(open_kiosk: bool = False) -> None:
    """Serve the kiosk and keep the queue moving until told to stop."""
    config = settings()
    service, kiosk = build(open_kiosk)

    print(f"Kiosk:   {kiosk.url}  (open this on the stand's screen, then F11)")
    if config.configured:
        print(f"Server:  {config.server}  as station {config.station!r}")
    else:
        # Not a failure. Results still queue on disk and the kiosk still draws
        # whatever was last cached; only the sending is off. Saying so plainly
        # is what stops somebody spending the morning of a fair wondering why
        # the board is empty.
        print("Server:  not configured — results will queue on disk.\n"
              "         Set WHEEL_RACER_SERVER and WHEEL_RACER_BOOTH_TOKEN "
              "in .env to send them.")

    stopping = False

    def stop(*_) -> None:
        nonlocal stopping
        stopping = True

    # Ctrl-C and a terminated child both mean the same thing here, and both
    # have to leave the live column tidy rather than leaving a stand on it that
    # has gone home.
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        while not stopping:
            service.tick()
            time.sleep(TICK_SECONDS)
    finally:
        service.closing()
        kiosk.stop()


__all__ = ["build", "run", "Kiosk", "StationService"]
