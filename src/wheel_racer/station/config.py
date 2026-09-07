"""Where this booth sends things, and what it calls itself.

Everything here comes from the environment rather than from `config.py`,
because two of the three values are a URL and a secret. `config.py` is the
booth's tuning file and is committed; a write token committed to a public
repository is a token that has to be changed, at a fair, from a phone.

Because setting environment variables before launching a game is not a thing
anyone wants to do on a laptop in a car park, a `.env` file at the top of the
repository is read too — it is already gitignored, it can be written once when
the stand is set up, and a real environment variable still wins over it.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENV_FILE = REPO / ".env"

# The port the kiosk browser is pointed at. High and unremarkable, because the
# laptop at a stand may well have something else running on the usual ones.
DEFAULT_PORT = 8752


@dataclass(frozen=True)
class StationSettings:
    """This laptop's half of the arrangement."""

    server: str
    """Where the public board lives, e.g. https://racer.example.com."""

    token: str
    station: str
    """What this booth is called on the live column.

    Defaults to the machine's own name. With one stand nobody ever sees it;
    with two, they tell themselves apart without anybody configuring anything.
    """

    port: int
    board_refresh_seconds: float
    heartbeat_seconds: float
    """How often to repeat an unchanged state, so the server can tell a quiet
    stand from one that has packed up."""

    @property
    def configured(self) -> bool:
        """Whether there is a server to talk to at all.

        Unconfigured is a supported way to run: the game still plays, results
        still queue up on disk, and the kiosk shows whatever was last cached.
        Only the sending stops. That is what makes it possible to set the stand
        up, and test it, before anybody has decided what the URL is.
        """
        return bool(self.server and self.token)


def _load_env_file() -> None:
    """Read `.env`, without letting it overrule the real environment.

    Deliberately not a parser. `KEY=value`, one per line, `#` for comments —
    the whole file is three lines written once by whoever sets the stand up,
    and anything cleverer would be a dependency or a surprise.
    """
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


@lru_cache(maxsize=1)
def settings() -> StationSettings:
    """This booth's configuration, read once."""
    _load_env_file()
    return StationSettings(
        server=os.environ.get("WHEEL_RACER_SERVER", "").strip(),
        token=os.environ.get("WHEEL_RACER_BOOTH_TOKEN", "").strip(),
        station=os.environ.get("WHEEL_RACER_STATION", "").strip() or _machine_name(),
        port=int(_float("WHEEL_RACER_KIOSK_PORT", DEFAULT_PORT)),
        board_refresh_seconds=_float("WHEEL_RACER_BOARD_REFRESH_S", 3.0),
        heartbeat_seconds=_float("WHEEL_RACER_HEARTBEAT_S", 5.0),
    )


def _machine_name() -> str:
    """A name for this laptop that a person would recognise on a screen.

    Trimmed to the first label, so `booth-laptop.local` shows as
    `booth-laptop`, and capped because the server's column is short.
    """
    try:
        name = socket.gethostname()
    except OSError:
        return "booth"
    return (name.split(".")[0] or "booth")[:32]
