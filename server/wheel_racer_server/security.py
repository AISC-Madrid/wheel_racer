"""Who is allowed to write, and how often.

Two secrets, both from the environment, both compared in constant time:

  * the **booth token**, held by the laptop at the stand. It may file runs, say
    who is driving, and ask what a returning player's best time is.
  * the **admin token**, held by whoever is running the fair. It may export the
    mailing list, hide a row, and erase a player.

Reading the board needs neither. That is the whole point of the thing — a
public screen and every phone in the room can have it, and none of them can
change a number.

A missing secret closes the door rather than opening it. A deployment that
forgot a variable then fails at the booth in a way somebody notices within a
minute, instead of succeeding for the entire internet in a way nobody notices
at all.
"""

from __future__ import annotations

import secrets
import time
from collections import deque

from fastapi import Header, HTTPException, status

from .config import settings


def _check(offered: str | None, expected: str, what: str) -> str:
    if not expected:
        # Not 401: nothing the caller can send would work. This is the server
        # admitting it was deployed wrong.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"this server has no {what} configured, so it accepts no writes",
        )

    presented = ""
    if offered and offered.lower().startswith("bearer "):
        presented = offered[7:].strip()

    # `compare_digest` rather than `==`, so a wrong token takes the same time
    # to reject however much of it was right.
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad token",
                            headers={"WWW-Authenticate": "Bearer"})
    return presented


async def booth(authorization: str | None = Header(default=None)) -> str:
    """Guard for everything the stand's laptop does."""
    token = _check(authorization, settings().booth_token, "booth token")
    _limiter.check(token)
    return token


async def admin(authorization: str | None = Header(default=None)) -> str:
    """Guard for the mailing list and for moderation."""
    return _check(authorization, settings().admin_token, "admin token")


class RateLimiter:
    """A fixed number of writes per minute, per token.

    Deliberately not applied to reading the board. The audience for this page
    is a room full of phones behind one conference wifi, which to a server is
    one IP address making hundreds of requests — the exact shape a naive rate
    limiter mistakes for an attack, and the failure mode would be the stand's
    own visitors being locked out of the thing the stand is advertising.

    Writing is different: it is authenticated, it comes from one laptop, and
    the first thing a leaked token does is fill the table with impossible lap
    times. Capping it costs the booth nothing and bounds that damage.

    In memory, so it resets when the container restarts. That is acceptable
    for what it defends against; anything stronger belongs in the proxy.
    """

    def __init__(self, window: float = 60.0) -> None:
        self.window = window
        self._seen: dict[str, deque[float]] = {}

    def check(self, key: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        allowed = settings().public_writes_per_minute
        recent = self._seen.setdefault(key, deque())

        while recent and recent[0] <= moment - self.window:
            recent.popleft()
        if len(recent) >= allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many writes — slow down",
                headers={"Retry-After": str(int(self.window))},
            )
        recent.append(moment)


_limiter = RateLimiter()
