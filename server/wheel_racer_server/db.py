"""The SQLite file: how it is opened, and how it comes to have tables.

SQLite rather than MySQL or Postgres, deliberately. A booth afternoon is a few
hundred rows written by exactly one process, and everything a bigger engine
buys — concurrent writers, network access, a connection pool — is either
unused here or is a thing we are actively trying not to have. The whole
database is one file, which means a backup is a copy and a restore is a paste.

That file holds email addresses, so it lives on a mounted volume outside the
directory the web server serves, and it never enters the repository.

Schema changes go on the end of `MIGRATIONS` and are applied in order, tracked
by SQLite's own `user_version`. No migration framework: a list of statements
and an integer is the whole mechanism, it is readable at a glance, and it runs
on startup so a redeploy is the upgrade.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import settings

# Each entry is one schema version, applied in order inside a transaction.
# Never edit an entry that has shipped — append a new one.
MIGRATIONS: list[str] = [
    # v1 — players, their runs, and what each booth is doing right now.
    """
    CREATE TABLE players (
        email_key         TEXT PRIMARY KEY,
        email             TEXT NOT NULL,
        name              TEXT NOT NULL,
        terms_version     TEXT NOT NULL,
        terms_accepted_at TEXT NOT NULL,
        first_seen_at     TEXT NOT NULL,
        last_seen_at      TEXT NOT NULL,
        hidden            INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE runs (
        id          TEXT PRIMARY KEY,
        email_key   TEXT NOT NULL REFERENCES players(email_key) ON DELETE CASCADE,
        seconds     REAL NOT NULL,
        station     TEXT NOT NULL,
        raced_at    TEXT NOT NULL,
        received_at TEXT NOT NULL
    );

    CREATE INDEX runs_by_player ON runs (email_key, seconds);

    CREATE TABLE stations (
        station    TEXT PRIMARY KEY,
        state      TEXT NOT NULL,
        driver     TEXT NOT NULL DEFAULT '',
        lap        INTEGER NOT NULL DEFAULT 0,
        laps       INTEGER NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL
    );
    """,
    # v2 — the line the board counts from, so a fair can start at nil without
    # anything being deleted. One row in a table of settings rather than a
    # column on anything: it is a property of the server, not of a player.
    """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the database, creating the file and its directory if need be.

    `check_same_thread=False` because the connection is handed to whichever
    worker thread the request lands on and is used by one at a time; the
    connection is per-request, not shared.
    """
    location = Path(path) if path is not None else settings().database
    if location != Path(":memory:"):
        location.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(location, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    # WAL so a reader — every phone looking at the board — never blocks the one
    # writer, which is the booth posting a result someone is waiting to see.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    # A run arriving while the file is briefly locked should wait, not fail:
    # the laptop would retry anyway, and a retry is slower than a wait.
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def migrate(connection: sqlite3.Connection) -> int:
    """Bring the file up to the current schema. Returns the version it is at.

    Idempotent, so it can run unconditionally on every startup — which is the
    point: deploying is `git push`, and the schema has to keep up on its own.
    """
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    for index, statements in enumerate(MIGRATIONS[version:], start=version + 1):
        with connection:
            connection.executescript(statements)
            # Not parameterisable — PRAGMA takes a literal. `index` is derived
            # from the length of a list in this file, never from input.
            connection.execute(f"PRAGMA user_version = {index}")
        version = index
    return version


@contextmanager
def session(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """A connection for the length of one request, closed however it ends."""
    connection = connect(path)
    try:
        yield connection
    finally:
        connection.close()
