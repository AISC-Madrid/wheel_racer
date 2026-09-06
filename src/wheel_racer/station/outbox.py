"""Runs that have happened but have not reached the server yet.

The booth's wifi is somebody else's. It is a conference hall's guest network, or
a phone hotspot, or a router behind a wall, and at some point in the afternoon
it will go away for twenty minutes. What must not happen when it does is a
person driving a personal best and being told the machine could not save it.

So the game does not send results. It writes them here — one small JSON file
per run, in a directory — and carries on. Something else, later, does the
sending. That is the whole idea:

  * **The game never blocks on the network.** Writing a file takes a
    microsecond and cannot time out.
  * **A crash loses nothing.** The file is on disk before the result screen is
    drawn, so pulling the power out mid-afternoon costs whatever has not been
    uploaded yet, and nothing that has been driven.
  * **Retrying is safe.** Every run carries an id minted here, and the server
    treats a second delivery of the same id as a no-op. The failure this
    recovers from is usually "the write worked and the reply did not", and
    without an id that case would double somebody's runs.

A directory of files rather than one appended log, because the operations that
matter are "add one" and "remove exactly the one that got through", and on a
directory both are single atomic filesystem calls. An append-only log would
need rewriting to delete from, which is the one thing that can lose the
entries either side of the one being removed.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "outbox"

# Runs are sent oldest first, so the board fills in the order things actually
# happened after an outage rather than backwards.
SUFFIX = ".run.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PendingRun:
    """One finished run, in the shape the server accepts.

    Field for field the server's `Submission`. Deliberately: this is what gets
    posted, and a translation step in between would be one more place for the
    two halves of the repository to disagree.
    """

    name: str
    email: str
    seconds: float
    station: str
    terms_version: str
    terms_accepted_at: str

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Minted here, at the booth, and never anywhere else.

    This is what makes blind retries safe, so it has to be decided by the side
    that knows a run happened — before the first attempt, not after a failure.
    """

    raced_at: str = field(default_factory=_now)

    def as_json(self) -> dict:
        return asdict(self)


class Outbox:
    """The directory, from both ends.

    The game holds one and calls `add`; the station service holds one and calls
    `pending` and `done`. Neither has to exist for the other to work, and
    either can be restarted at any point without the other noticing — the same
    arrangement the live channel already has, for the same booth reasons.
    """

    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)

    def add(self, run: PendingRun) -> Path:
        """Put a run on the queue. Returns where it landed.

        Written to a temporary file and moved into place, so the service can
        never pick up a half-written run — `os.replace` within a directory is
        atomic, and the reader either sees a whole file or no file.
        """
        self.path.mkdir(parents=True, exist_ok=True)
        destination = self.path / f"{run.id}{SUFFIX}"

        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path,
            prefix=".pending-", suffix=".tmp", delete=False,
        )
        try:
            with handle:
                json.dump(run.as_json(), handle)
            os.replace(handle.name, destination)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
        return destination

    def pending(self) -> list[tuple[Path, dict]]:
        """Everything waiting, oldest first.

        A file that cannot be read or parsed is skipped rather than raised on.
        One unreadable run must not stop the twenty behind it from reaching the
        board — losing a row is a smaller failure than losing the afternoon.
        """
        if not self.path.is_dir():
            return []

        waiting = []
        for file in sorted(self.path.glob(f"*{SUFFIX}"), key=_age):
            try:
                with file.open(encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict):
                waiting.append((file, payload))
        return waiting

    def done(self, file: Path) -> None:
        """This run reached the server. `missing_ok` because two passes racing
        over the same file is not worth preventing — the server would ignore
        the duplicate anyway."""
        file.unlink(missing_ok=True)

    def count(self) -> int:
        """How many runs are waiting. Shown at the stand, so somebody setting
        up can see at a glance whether the queue is draining."""
        return len(list(self.path.glob(f"*{SUFFIX}"))) if self.path.is_dir() else 0


def _age(file: Path) -> float:
    """Sort key: when the run was written.

    Falls back to the far future for a file that has vanished between the glob
    and the stat, which sorts it last and lets `pending` skip it quietly.
    """
    try:
        return file.stat().st_mtime
    except OSError:
        return float("inf")
