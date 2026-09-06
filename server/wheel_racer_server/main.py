"""The HTTP surface: what the booth may say, and what the room may read.

Deliberately small. Five endpoints for the stand, three for whoever is running
it, and one that the public board polls. Everything that thinks lives in
`store.py`; the functions here turn requests into calls and results into status
codes, and nothing else.

The shape follows one rule: **reading is open, writing is not.** A phone
scanning the QR code on the stand gets `/api/board` with no credentials at all,
and there is no path from a browser to a number changing.

Two smaller decisions worth knowing:

  * **Addresses go in bodies, never in paths.** `POST /api/players/lookup`
    instead of `GET /api/players/{email}`. A URL ends up in access logs, proxy
    histories and error reports; a body does not, and every one of those places
    is somewhere an email address has no business being.
  * **No CORS.** The page and the API are the same origin, both here and on the
    booth laptop, so there is nothing to relax and no third site that needs to
    call this.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import security, store
from .config import TERMS_URL, TERMS_VERSION, settings
from .db import connect, migrate, session
from .models import (
    Board,
    Erasure,
    Health,
    Lookup,
    Moderation,
    PlayerSummary,
    Receipt,
    StationUpdate,
    Submission,
)

# The front end is shared with the booth's own kiosk server, so it lives at the
# top of the repository rather than inside this package. The container copies
# it to a fixed place and sets no variable; a developer running from a checkout
# gets it found for them.
REPO = Path(__file__).resolve().parents[2]
WEB_ROOT = REPO / "web"
# The stand's logo, served from where the game already keeps it. Copying it
# into `web/` would put the same image in the repository twice, and the two
# copies would drift the first time anybody changed it.
ASSETS = REPO / "assets"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bring the schema up to date before serving anything.

    On startup rather than in a deploy step, because deploying this is `git
    push` and there is no step to put it in. It is idempotent, so a container
    that restarts three times in a row does the work once.
    """
    with session() as connection:
        migrate(connection)
    yield


app = FastAPI(
    title="Hand-Wheel Racer",
    summary="Lap times from the booth, and the board the room reads.",
    lifespan=lifespan,
)


def database() -> Iterator[sqlite3.Connection]:
    """One connection per request, closed however the request ends."""
    with session() as connection:
        yield connection


# --- the public board --------------------------------------------------------

@app.get("/api/board", response_model=Board)
def board(limit: int | None = None,
          connection: sqlite3.Connection = Depends(database)) -> Board:
    """Everything the page shows, in one response.

    `limit` is clamped rather than validated: this is the one endpoint the
    whole room can call, and it must not be possible to ask it for a hundred
    thousand rows.
    """
    config = settings()
    rows = config.board_size if limit is None else max(1, min(limit, 100))
    return store.board(connection, rows, config.station_stale_seconds)


@app.get("/health", response_model=Health)
def health(connection: sqlite3.Connection = Depends(database)) -> Health:
    """Is it up, and was it deployed with everything it needs?

    Reports `configured` honestly. A server missing its tokens serves the board
    perfectly and accepts nothing from the booth, which is a state worth being
    able to see from outside rather than discovering at the stand.
    """
    return Health(
        ok=True,
        configured=settings().configured,
        players=store.count_players(connection),
        runs=store.count_runs(connection),
        terms_url=TERMS_URL,
        terms_version=TERMS_VERSION,
    )


# --- what the booth says -----------------------------------------------------

@app.post("/api/runs", response_model=Receipt,
          dependencies=[Depends(security.booth)])
def submit(run: Submission,
           connection: sqlite3.Connection = Depends(database)) -> Receipt:
    """File one finished run.

    Safe to call twice with the same id, which is what lets the booth keep a
    queue on disk and retry it blindly after the wifi comes back.
    """
    config = settings()
    if not config.min_lap_seconds <= run.seconds <= config.max_lap_seconds:
        # Not a judgement about driving. No real lap lands outside this window,
        # and the first thing a stolen token does is fill the board with times
        # nobody can beat.
        # The literal rather than the constant: starlette renamed it, and this
        # module has no business caring which version is installed.
        raise HTTPException(422, f"a lap of {run.seconds}s is not a lap anyone drove")
    return store.record(connection, run)


@app.post("/api/players/lookup", response_model=PlayerSummary,
          dependencies=[Depends(security.booth)])
def lookup(request: Lookup,
           connection: sqlite3.Connection = Depends(database)) -> PlayerSummary:
    """What this address has done before, so the booth can say what to beat.

    A 404 here is an ordinary answer, not a problem: it means somebody new is
    standing at the machine.
    """
    found = store.summary(connection, request.email)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "never played")
    return found


@app.post("/api/stations", status_code=status.HTTP_204_NO_CONTENT,
          dependencies=[Depends(security.booth)])
def station(update: StationUpdate,
            connection: sqlite3.Connection = Depends(database)) -> Response:
    """Who is at this booth's wheel, and a heartbeat while nobody is.

    Called on every change and then slowly on repeat, so the board can tell
    "this stand is quiet" from "this stand has gone home".
    """
    store.see_station(connection, update)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/api/stations/{name}", status_code=status.HTTP_204_NO_CONTENT,
            dependencies=[Depends(security.booth)])
def close_station(name: str,
                  connection: sqlite3.Connection = Depends(database)) -> Response:
    """Packing up. Take this stand off the live column now, not in twenty
    seconds when it goes stale on its own."""
    store.forget_station(connection, name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- running the fair --------------------------------------------------------

@app.get("/api/admin/players.csv", dependencies=[Depends(security.admin)])
def export(connection: sqlite3.Connection = Depends(database)) -> Response:
    """The mailing list, with the consent that justifies it attached.

    A CSV because the thing on the other end of this is a newsletter tool, and
    every one of them reads a CSV.
    """
    rows = store.export(connection)
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)

    return Response(
        # `utf-8-sig`: Excel opens a plain UTF-8 CSV as mojibake, and the names
        # in this file are Spanish. The BOM is what makes accents survive the
        # double-click.
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="players.csv"',
                 # It is a list of email addresses. Nothing caches it.
                 "Cache-Control": "no-store"},
    )


@app.post("/api/admin/players/hide", status_code=status.HTTP_204_NO_CONTENT,
          dependencies=[Depends(security.admin)])
def hide(request: Moderation,
         connection: sqlite3.Connection = Depends(database)) -> Response:
    """Take a name off the public board, or put it back.

    The thing this exists for happens in the middle of a fair: somebody signs
    in as something that must not be on a screen behind a stand, and goes third
    fastest. It has to be one call from a phone.
    """
    if not store.set_hidden(connection, request.email, request.hidden):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such player")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/admin/players/erase", status_code=status.HTTP_204_NO_CONTENT,
          dependencies=[Depends(security.admin)])
def erase(request: Erasure,
          connection: sqlite3.Connection = Depends(database)) -> Response:
    """Delete a player and every run they set. The answer to "remove my data"."""
    if not store.erase(connection, request.email):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such player")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- the page ----------------------------------------------------------------

# Mounted last, so a future endpoint can never be shadowed by a file that
# happens to share its name.
if ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")

if WEB_ROOT.is_dir():
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        # `no-cache` rather than `no-store`: the browser may keep the file and
        # must ask whether it changed. A kiosk left running for eight hours
        # should pick up a fix without anyone walking over to reload it.
        return FileResponse(WEB_ROOT / "index.html",
                            headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=WEB_ROOT), name="web")


__all__ = ["app", "connect", "migrate"]
