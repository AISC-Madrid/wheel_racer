"""Every question the server asks the database, and every change it makes.

Endpoints in `main.py` do HTTP; this module does data. Nothing here imports
FastAPI, which is what makes it testable against a `:memory:` database in a few
lines and what keeps request handling from quietly growing SQL.

Two things are worth knowing before reading the queries:

  * **A player's best time is derived, never stored.** It is `MIN(seconds)`
    over their runs. A stored "best" column is a second copy of a fact, and a
    second copy is a thing that can disagree — after a moderation delete, or
    after a run arrives late from a queue that spent an hour offline. At this
    size the query is free.
  * **Every run is kept.** The CSV this replaces held one row per player and
    threw away everything that was not their record. Keeping the runs costs
    nothing, is what makes the retry-safe queue possible at all, and means
    "how many people played on Saturday" is a question with an answer.
  * **The board counts from a line, not from the beginning.** An afternoon of
    setting the stand up is an afternoon of lap times that must not be on the
    screen when the doors open, and the answer is a cutoff rather than a
    delete: move the line, and everything before it stops counting while
    staying in the file for the export. Every query below that a visitor can
    see the result of respects it, and `export` deliberately does not.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .models import (
    Board,
    Driver,
    PlayerSummary,
    Receipt,
    Standing,
    StationUpdate,
    Submission,
)

# Times are kept to the hundredth: what the booth's clock shows, what the CSV
# stored before this, and what the board renders. Rounding on the way in rather
# than on the way out means the number in the database is the number on screen.
PRECISION = 2

# The row in `meta` holding the cutoff, and the value that means "count
# everything". The empty string rather than NULL, because every stored date is
# an ISO 8601 stamp and every ISO 8601 stamp sorts after "" — so the filter is
# the same comparison whether a fair has been reset or not, and there is no
# second version of any query below to keep in step with the first.
BOARD_SINCE = "board_since"
ALL_TIME = ""


def normalise_email(email: str) -> str:
    """The form an address is stored and compared in.

    Deliberately identical to `wheel_racer.players.normalise_email`, and
    deliberately duplicated: the server cannot import the game, and this rule
    is what decides whether a returning player finds their own record. If one
    side ever changes, the other has to change with it.
    """
    return email.strip().lower()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    """ISO 8601 in UTC, the only format stored anywhere in here.

    A booth laptop, a container and whoever reads the export next year are
    three different ideas of "local time". Picking UTC once at the door means
    no other line in this file has to think about it.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


# --- where the board counts from ---------------------------------------------

def board_since(connection: sqlite3.Connection) -> str:
    """The moment the current fair started, or `ALL_TIME` if it never has."""
    row = connection.execute(
        "SELECT value FROM meta WHERE key = ?", (BOARD_SINCE,)
    ).fetchone()
    return row["value"] if row is not None else ALL_TIME


def set_board_since(connection: sqlite3.Connection,
                    moment: datetime | None) -> str:
    """Start the board again from `moment`, or from the beginning if None.

    Nothing is deleted and nothing is unrecoverable: this moves a line, and
    moving it back brings every run before it into view again. That is the
    whole reason it is a stored timestamp rather than a `DELETE`.
    """
    stamp = ALL_TIME if moment is None else _stamp(moment)
    with connection:
        if stamp == ALL_TIME:
            connection.execute("DELETE FROM meta WHERE key = ?", (BOARD_SINCE,))
        else:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (BOARD_SINCE, stamp),
            )
    return stamp


# --- writing -----------------------------------------------------------------

def record(connection: sqlite3.Connection, run: Submission) -> Receipt:
    """Store one run and say what it did to that player's standing.

    Idempotent by run id. A submission that has already been stored is reported
    as a duplicate and changes nothing — which is exactly what the booth's
    retry queue needs, because the failure it recovers from is usually "the
    write succeeded and the reply did not".
    """
    key = normalise_email(run.email)
    seconds = round(run.seconds, PRECISION)
    now = _stamp(_now())

    with connection:
        known = connection.execute(
            "SELECT 1 FROM runs WHERE id = ?", (run.id,)
        ).fetchone() is not None

        _touch_player(connection, key, run, now)

        # Asked before the insert, because "did they beat it" is "did the
        # stored value move". Comparing against a best that already included
        # this run would call every first run an improvement and every tie a
        # personal best.
        previous = _best(connection, key)

        if not known:
            connection.execute(
                "INSERT INTO runs (id, email_key, seconds, station, raced_at,"
                " received_at) VALUES (?, ?, ?, ?, ?, ?)",
                (run.id, key, seconds, run.station, _stamp(run.raced_at), now),
            )

    best = _best(connection, key)
    return Receipt(
        duplicate=known,
        # A player always has at least the run just filed, so this is only None
        # if something has gone badly wrong; fall back to the submitted time
        # rather than handing the booth a null it has no screen for.
        best_seconds=best if best is not None else seconds,
        previous_best=previous,
        # A retry must never claim a personal best twice: the screen that would
        # have celebrated it was handed to the next person long ago.
        improved=not known and (previous is None or seconds < previous),
        position=position(connection, key),
        players=count_players(connection),
    )


def _touch_player(connection: sqlite3.Connection, key: str,
                  run: Submission, now: str) -> None:
    """Create the player, or bring their row up to date.

    A returning player's latest spelling of their own name wins — it is more
    likely to be the correction than the mistake — and so does their latest
    consent stamp, so the row always answers "what did they last agree to".
    """
    connection.execute(
        """
        INSERT INTO players (email_key, email, name, terms_version,
                             terms_accepted_at, first_seen_at, last_seen_at)
        VALUES (:key, :email, :name, :terms, :accepted, :now, :now)
        ON CONFLICT (email_key) DO UPDATE SET
            email             = excluded.email,
            name              = excluded.name,
            terms_version     = excluded.terms_version,
            terms_accepted_at = excluded.terms_accepted_at,
            last_seen_at      = excluded.last_seen_at
        """,
        {
            "key": key,
            "email": run.email.strip(),
            "name": run.name.strip(),
            "terms": run.terms_version,
            "accepted": _stamp(run.terms_accepted_at),
            "now": now,
        },
    )


def see_station(connection: sqlite3.Connection, update: StationUpdate,
                now: float | None = None) -> None:
    """Record what a booth is doing, replacing whatever it said before."""
    with connection:
        connection.execute(
            """
            INSERT INTO stations (station, state, driver, lap, laps, updated_at)
            VALUES (:station, :state, :driver, :lap, :laps, :now)
            ON CONFLICT (station) DO UPDATE SET
                state = excluded.state, driver = excluded.driver,
                lap = excluded.lap, laps = excluded.laps,
                updated_at = excluded.updated_at
            """,
            {**update.model_dump(),
             "now": now if now is not None else _now().timestamp()},
        )


def forget_station(connection: sqlite3.Connection, station: str) -> None:
    """Drop a booth from the live column — it has packed up for the day."""
    with connection:
        connection.execute("DELETE FROM stations WHERE station = ?", (station,))


# --- reading -----------------------------------------------------------------

def _best(connection: sqlite3.Connection, key: str) -> float | None:
    """This player's record, as far as the current fair is concerned.

    Scoped to the cutoff like everything else the booth shows, so that after a
    reset the first run of the day is a personal best and gets its confetti,
    instead of being measured against a time set while the stand was being
    tested and which is now on no screen anywhere.
    """
    row = connection.execute(
        "SELECT MIN(seconds) AS best FROM runs"
        " WHERE email_key = ? AND raced_at >= ?",
        (key, board_since(connection)),
    ).fetchone()
    return row["best"] if row and row["best"] is not None else None


def position(connection: sqlite3.Connection, key: str) -> int | None:
    """Where this player sits on the board, or None if they are not on it.

    Counted as "how many people are ahead" rather than by paging the table, so
    a player buried in the field still gets a real number to be told.
    """
    best = _best(connection, key)
    if best is None or _is_hidden(connection, key):
        return None

    ahead = connection.execute(
        """
        SELECT COUNT(*) AS ahead FROM (
            SELECT MIN(r.seconds) AS best
            FROM runs r JOIN players p ON p.email_key = r.email_key
            WHERE p.hidden = 0 AND r.email_key != ? AND r.raced_at >= ?
            GROUP BY r.email_key
        ) WHERE best < ?
        """,
        (key, board_since(connection), best),
    ).fetchone()["ahead"]
    return ahead + 1


def _is_hidden(connection: sqlite3.Connection, key: str) -> bool:
    row = connection.execute(
        "SELECT hidden FROM players WHERE email_key = ?", (key,)
    ).fetchone()
    return row is not None and bool(row["hidden"])


def count_players(connection: sqlite3.Connection) -> int:
    """How many people have finished a run, hidden ones excluded."""
    return connection.execute(
        """
        SELECT COUNT(DISTINCT r.email_key) AS total
        FROM runs r JOIN players p ON p.email_key = r.email_key
        WHERE p.hidden = 0 AND r.raced_at >= ?
        """,
        (board_since(connection),),
    ).fetchone()["total"]


def count_runs(connection: sqlite3.Connection) -> int:
    """How many laps this fair has seen. Not how many the file holds."""
    return connection.execute(
        "SELECT COUNT(*) AS total FROM runs WHERE raced_at >= ?",
        (board_since(connection),),
    ).fetchone()["total"]


def summary(connection: sqlite3.Connection, email: str) -> PlayerSummary | None:
    """What is known about one address, or None if it has never played.

    This is what a booth asks at sign-in, and it is the reason a player who
    drove at a different stand on a different laptop is still told what they
    have to beat.
    """
    key = normalise_email(email)
    row = connection.execute(
        "SELECT name FROM players WHERE email_key = ?", (key,)
    ).fetchone()
    if row is None:
        return None

    runs = connection.execute(
        "SELECT COUNT(*) AS total FROM runs"
        " WHERE email_key = ? AND raced_at >= ?",
        (key, board_since(connection)),
    ).fetchone()["total"]
    if not runs:
        # Known to the file, but not to this fair. Answering with a record the
        # board does not show would put a time on the sign-in screen that the
        # player cannot find anywhere behind them, so they are somebody new —
        # which, on the only afternoon that counts, they are.
        return None
    return PlayerSummary(name=row["name"], best_seconds=_best(connection, key),
                         runs=runs, position=position(connection, key))


def standings(connection: sqlite3.Connection, limit: int) -> list[Standing]:
    """The board: quickest first, first names only.

    Ties are broken by who got there first, which is both the fair answer and
    a stable one — a board whose rows swap places on every refresh looks
    broken.
    """
    rows = connection.execute(
        """
        SELECT p.email_key, p.name, MIN(r.seconds) AS best
        FROM runs r JOIN players p ON p.email_key = r.email_key
        WHERE p.hidden = 0 AND r.raced_at >= ?
        GROUP BY p.email_key
        ORDER BY best ASC, p.first_seen_at ASC
        LIMIT ?
        """,
        (board_since(connection), limit),
    ).fetchall()
    if not rows:
        return []

    leader = rows[0]["best"]
    labels = public_names([(row["email_key"], row["name"]) for row in rows])
    return [
        Standing(position=index, name=labels[row["email_key"]],
                 seconds=row["best"], gap=round(row["best"] - leader, PRECISION))
        for index, row in enumerate(rows, start=1)
    ]


def live(connection: sqlite3.Connection, stale_after: float,
         now: float | None = None) -> list[Driver]:
    """The booths that have said something recently enough to be believed.

    A stand that has been packed away still has a row in the table; it stops
    appearing here rather than being deleted, so a laptop that is merely
    rebooting comes back as itself instead of as a second station.
    """
    moment = now if now is not None else _now().timestamp()
    rows = connection.execute(
        "SELECT * FROM stations WHERE updated_at > ? ORDER BY station",
        (moment - stale_after,),
    ).fetchall()
    return [
        Driver(station=row["station"], state=row["state"],
               driver=first_name(row["driver"]), lap=row["lap"], laps=row["laps"])
        for row in rows
    ]


def board(connection: sqlite3.Connection, limit: int,
          stale_after: float, now: float | None = None) -> Board:
    """The whole public page, as one object."""
    return Board(
        standings=standings(connection, limit),
        live=live(connection, stale_after, now),
        players=count_players(connection),
        runs=count_runs(connection),
        generated_at=_now(),
        since=board_since(connection) or None,
    )


# --- names -------------------------------------------------------------------

def first_name(name: str) -> str:
    """The part of a name that may go on a screen strangers can photograph."""
    stripped = name.strip()
    return stripped.split(" ")[0] if stripped else ""


def public_names(people: list[tuple[str, str]]) -> dict[str, str]:
    """First names for a set of rows, with collisions broken by an initial.

    Sixty people at a stand in Madrid will contain two Martas, and a board with
    two identical rows on it is a board nobody believes. Only names that
    actually clash *among the rows being shown* get an initial: adding one to
    everybody would put surnames on a public screen for no reason, and adding a
    number would read as a bug.

    Keyed by email, because the display name is precisely the thing that is not
    unique here.
    """
    firsts = {key: first_name(name) for key, name in people}
    clashing = {
        first for first in firsts.values()
        if first and sum(1 for value in firsts.values() if value == first) > 1
    }
    if not clashing:
        return firsts

    labels = dict(firsts)
    for key, name in people:
        if firsts[key] not in clashing:
            continue
        surname = next((part for part in name.strip().split(" ")[1:] if part), "")
        if surname:
            labels[key] = f"{firsts[key]} {surname[0].upper()}."
    return labels


# --- moderation and export ---------------------------------------------------

def set_hidden(connection: sqlite3.Connection, email: str, hidden: bool) -> bool:
    """Take a player off the public board, or put them back.

    A flag rather than a delete, because the reason to reach for this mid-fair
    is a name nobody wants on a screen — and that is a reason to stop showing a
    row, not a reason to destroy a result somebody earned. Erasing is a
    separate and more deliberate act.
    """
    with connection:
        changed = connection.execute(
            "UPDATE players SET hidden = ? WHERE email_key = ?",
            (1 if hidden else 0, normalise_email(email)),
        ).rowcount
    return changed > 0


def erase(connection: sqlite3.Connection, email: str) -> bool:
    """Remove a player and every run they have ever set.

    This is the answer to "please delete my data", so it has to be a real
    delete and it has to take the runs with it — which the foreign key does.
    """
    with connection:
        changed = connection.execute(
            "DELETE FROM players WHERE email_key = ?", (normalise_email(email),)
        ).rowcount
    return changed > 0


def export(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every player with their consent stamp, for the newsletter.

    Consent travels with the address on purpose. An export that is only names
    and emails is an export somebody will one day have to justify, and the two
    columns that justify it are right here.

    The one reader that ignores the board's cutoff. Somebody who drove during
    the morning's testing still agreed to the terms and still asked for the
    newsletter, and a reset is a decision about a screen — not about who the
    stand met.
    """
    return connection.execute(
        """
        SELECT p.name, p.email, p.terms_version, p.terms_accepted_at,
               p.first_seen_at, p.last_seen_at, p.hidden,
               MIN(r.seconds) AS best_seconds, COUNT(r.id) AS runs
        FROM players p LEFT JOIN runs r ON r.email_key = p.email_key
        GROUP BY p.email_key
        ORDER BY p.first_seen_at
        """
    ).fetchall()
