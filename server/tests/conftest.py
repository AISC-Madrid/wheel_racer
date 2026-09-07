"""Fixtures for the server's tests.

Every test gets its own database file and its own tokens. Nothing here talks to
the network, nothing needs a container, and the whole suite runs in about a
second — which is the point, because the alternative is testing the booth's
one-shot afternoon by hand.
"""

from __future__ import annotations

import pytest

# The server is an optional extra, so a checkout set up only for the game runs
# `pytest` and sees these skipped rather than a wall of import errors.
pytest.importorskip("fastapi", reason="the `server` extra is not installed")
pytest.importorskip("httpx", reason="starlette's test client needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from wheel_racer_server import security  # noqa: E402
from wheel_racer_server.config import settings  # noqa: E402
from wheel_racer_server.db import connect, migrate  # noqa: E402
from wheel_racer_server.main import app  # noqa: E402

BOOTH_TOKEN = "booth-token-for-tests"
ADMIN_TOKEN = "admin-token-for-tests"


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """A server pointed at an empty database, with both tokens set."""
    monkeypatch.setenv("WHEEL_RACER_DB", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("WHEEL_RACER_BOOTH_TOKEN", BOOTH_TOKEN)
    monkeypatch.setenv("WHEEL_RACER_ADMIN_TOKEN", ADMIN_TOKEN)
    # `settings` is cached for the life of a process, which is right in
    # production and wrong in a test file that changes the environment between
    # cases.
    settings.cache_clear()
    # The limiter is module-level state and would otherwise carry one test's
    # requests into the next one's budget.
    security._limiter = security.RateLimiter()
    yield settings()
    settings.cache_clear()


@pytest.fixture
def db(configured):
    """A migrated connection to the same file the app is using."""
    connection = connect()
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def client(configured):
    """The app, with its startup hook run so the schema exists."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def booth():
    return {"Authorization": f"Bearer {BOOTH_TOKEN}"}


@pytest.fixture
def admin():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def run_payload(**overrides):
    """A valid submission, with the fields a test cares about replaced."""
    payload = {
        "id": "00000000-0000-4000-8000-000000000001",
        "name": "Marta Ruiz",
        "email": "Marta@Example.com",
        "seconds": 14.88,
        "station": "booth-1",
        "raced_at": "2026-03-14T11:02:03+01:00",
        "terms_version": "2026-01",
        "terms_accepted_at": "2026-03-14T11:00:00+01:00",
    }
    payload.update(overrides)
    return payload
