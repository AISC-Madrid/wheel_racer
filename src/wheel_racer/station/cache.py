"""The two things the booth keeps a local copy of, and why.

Neither is the truth. The server holds the truth, and that is the point of the
whole exercise — a player who drove here last month on a different laptop still
gets their record back. These are mirrors, kept so that the stand keeps working
during the twenty minutes the venue's wifi is away:

  * **The board.** Written every time the server is asked for it, read by the
    kiosk screen when it cannot be. Without it, losing the wifi at a fair means
    the big screen behind the stand goes blank in front of a queue, which is
    the exact failure the whole offline queue exists to prevent.
  * **The roster.** Who has signed in at this laptop and what their best is, so
    that somebody having a second go five minutes later is still told what they
    have to beat. Small, and only ever the people who walked up to this
    machine.

The roster holds email addresses, so it lives under `data/` with everything
else the booth collects — gitignored, and never committed.

Both are written the way the rest of this project writes files: to a temporary
file alongside, then moved into place. A reader polling one of them must never
catch it half-written.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..atomicfile import write_json

DATA = Path(__file__).resolve().parents[3] / "data"
BOARD_PATH = DATA / "board.json"
ROSTER_PATH = DATA / "known.json"


def _write(path: Path, payload: dict) -> bool:
    """Save one of the caches. Returns whether it actually landed.

    Both of these files are written by two processes — the roster by the
    game after a run and by the station after a lookup — so on Windows a
    write can arrive while the other one has the file open. `atomicfile`
    waits that out; if it still cannot be written, the value is kept in
    memory and the next write puts it right.

    A cache that could not be saved is not worth ending anybody's run
    over, and this one is called from inside `record`.
    """
    try:
        write_json(path, payload, prefix=f".{path.stem}-")
    except OSError:
        return False
    return True


def _read(path: Path) -> dict | None:
    """The file, or None if it is missing, unreadable or not a JSON object.

    Never raises. Every caller here is either drawing a screen at a stand or
    signing somebody in, and neither has anything useful to do with an
    exception about a cache file.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


class BoardCache:
    """The last board the server gave us, kept on disk and in memory."""

    def __init__(self, path: Path | str = BOARD_PATH) -> None:
        self.path = Path(path)
        self._board: dict | None = _read(self.path)
        self._fetched_at: float = 0.0

    def store(self, board: dict, now: float | None = None) -> None:
        self._board = board
        self._fetched_at = time.monotonic() if now is None else now
        _write(self.path, board)

    def latest(self) -> dict | None:
        """What to draw. None only before the server has ever been reached."""
        return self._board

    def age(self, now: float | None = None) -> float | None:
        """Seconds since the server last answered, or None if it never has.

        The kiosk page shows this as a quiet dot rather than as a message: a
        screen at a stand should not be explaining its own plumbing to the
        public, but somebody setting up needs to be able to tell "the wifi is
        gone" from "nobody has played yet".
        """
        if not self._fetched_at:
            return None
        return (time.monotonic() if now is None else now) - self._fetched_at


class Roster:
    """Who has played at this laptop, and what their best time is.

    Keyed by the normalised address, the same way the server keys players. The
    entries are whatever the server last said about them, so a value here is
    never invented locally — it is either something the server confirmed or the
    result of a run this machine has already queued.
    """

    def __init__(self, path: Path | str = ROSTER_PATH) -> None:
        self.path = Path(path)
        self._people: dict[str, dict] = (_read(self.path) or {})

    def get(self, email_key: str) -> dict | None:
        return self._people.get(email_key)

    def best_for(self, email_key: str) -> float | None:
        entry = self._people.get(email_key)
        best = entry.get("best_seconds") if entry else None
        return float(best) if isinstance(best, (int, float)) else None

    def forget_everyone(self) -> None:
        """Empty the roster, because the fair it belonged to is over.

        Called when the server says the board now counts from a later moment
        than the one this laptop last heard about. What is cached here is
        exactly what the reset was for — a morning of testing, and the record
        it left in front of the first person to walk up.
        """
        self._people = {}
        _write(self.path, self._people)

    def remember(self, email_key: str, name: str, best_seconds: float | None,
                 **extra) -> None:
        """Record what is known about somebody.

        The best time only ever moves downwards. A stale answer from the server
        arriving after a quicker local run must not undo it — the run is
        already queued, and the server will agree in a moment.
        """
        entry = dict(self._people.get(email_key) or {})
        entry.update(extra)
        entry["name"] = name

        known = entry.get("best_seconds")
        candidates = [value for value in (known, best_seconds)
                      if isinstance(value, (int, float))]
        if candidates:
            entry["best_seconds"] = min(candidates)

        entry["seen_at"] = time.time()
        self._people[email_key] = entry
        _write(self.path, self._people)

    def __len__(self) -> int:
        return len(self._people)
