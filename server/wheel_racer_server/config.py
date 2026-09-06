"""Everything the server reads from its environment, in one place.

The booth laptop is configured by editing `config.py` and restarting; the
server is configured by environment variables, because it is deployed by
Coolify from this repository and nothing machine-specific may ever be
committed. Same idea, different mechanism: one module that knows about the
outside world, and nothing else in the package touching `os.environ`.

Two rules run through the defaults below:

  * **Absent secret means closed, never open.** A missing write token does not
    fall back to accepting anything; it makes every write fail loudly. A
    server deployed with half its variables set is a server that rejects the
    booth, which someone notices in a minute, rather than one that accepts the
    entire internet, which nobody notices at all.
  * **Everything else has a working default.** The only variables you *must*
    set to have a running server are the two tokens and the database path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# The consent text the sign-in form points at, and the version stamped on every
# player row. The URL belongs to AISC and is shown at the booth; the version is
# ours, and exists so that a future change to those terms is answerable — "who
# agreed to what" is the one question you cannot reconstruct after the fact.
TERMS_URL = "https://aiscmadrid.com/terms_conditions.php"
TERMS_VERSION = "2026-01"


@dataclass(frozen=True)
class Settings:
    """The server's whole configuration."""

    database: Path
    booth_token: str
    admin_token: str

    board_size: int
    """How many rows the public board shows. Eight, like the tower it replaces."""

    station_stale_seconds: float
    """No word from a booth for this long and it stops being 'driving now'.

    Generous next to the station heartbeat: a screen that drops the driver
    every time a laptop stutters is worse than one that takes a few seconds to
    notice a stand has packed up.
    """

    min_lap_seconds: float
    max_lap_seconds: float
    """The window a lap time has to fall in to be believed.

    Not tuning — a stolen write token's first move is a table full of 0.01s
    laps, and there is no legitimate submission outside these bounds. Wide
    enough that a genuinely odd run still counts.
    """

    public_writes_per_minute: int
    """Rate limit for the write endpoints, per client address."""

    @property
    def configured(self) -> bool:
        """Whether writes are possible at all.

        Read by the health endpoint, so a deployment missing its tokens says so
        out loud instead of waiting for a booth to discover it.
        """
        return bool(self.booth_token and self.admin_token)


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        # A typo in a deployment variable must not take the server down: the
        # board staying up matters more than honouring an unreadable override.
        return default


def _int(name: str, default: int) -> int:
    return int(_float(name, default))


@lru_cache(maxsize=1)
def settings() -> Settings:
    """The configuration, read once.

    Cached because it is asked for on every request and the environment does
    not change under a running process. Tests clear the cache rather than
    passing settings around by hand.
    """
    return Settings(
        # Defaults to a path inside the container's persistent volume. On a
        # developer's machine it lands in the same gitignored `data/` directory
        # the booth already keeps its files in.
        database=Path(os.environ.get("WHEEL_RACER_DB", "/data/wheel-racer.sqlite3")),
        booth_token=os.environ.get("WHEEL_RACER_BOOTH_TOKEN", ""),
        admin_token=os.environ.get("WHEEL_RACER_ADMIN_TOKEN", ""),
        board_size=_int("WHEEL_RACER_BOARD_SIZE", 8),
        station_stale_seconds=_float("WHEEL_RACER_STATION_STALE_S", 20.0),
        min_lap_seconds=_float("WHEEL_RACER_MIN_LAP_S", 5.0),
        max_lap_seconds=_float("WHEEL_RACER_MAX_LAP_S", 600.0),
        public_writes_per_minute=_int("WHEEL_RACER_WRITES_PER_MINUTE", 120),
    )
