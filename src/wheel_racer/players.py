"""Who has played, and the best time they managed. One CSV, three columns.

Deliberately the smallest thing that works. A booth session is a few dozen
people, so the whole file is read and rewritten on every save rather than doing
anything clever — at this size it is instant, and it means the file on disk is
always complete and always readable in a spreadsheet.

The file holds personal data, so it lives under `data/`, which is gitignored.
Nothing here should ever be committed.

Email is the identity. Two people at a booth share a name far more often than
they share an address, and a returning player who types their email again gets
their old best back rather than starting a second row.
"""

from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

FIELDS = ("name", "email", "best_seconds")

# Times are kept to the hundredth, which is what the clock on screen shows and
# what the file stores. Rounding on the way in rather than on the way out keeps
# the value in memory identical to the value on disk — otherwise the same run
# counts as a personal best before a reload and not after it.
PRECISION = 2
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "players.csv"


@dataclass(frozen=True)
class Player:
    """One row of the file."""

    name: str
    email: str
    best_seconds: float
    """Their quickest single lap, in seconds — not the total for a run.

    The column keeps its plain name because the file is already collecting
    results and renaming it would drop every row that has been gathered so far.
    """


def normalise_email(email: str) -> str:
    """The form the address is stored and compared in.

    Case and stray spaces are how the same person ends up in the file twice,
    and at a booth people type their address in a hurry.
    """
    return email.strip().lower()


class PlayerBook:
    """The CSV, as something the game can ask questions of."""

    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)

    def all(self) -> list[Player]:
        """Everyone on file, in no particular order.

        A malformed row is skipped rather than raised. The file is the booth's
        record of the afternoon; one bad line must not take the game down with
        a queue waiting, and losing one row is a smaller failure than losing
        the session.
        """
        if not self.path.is_file():
            return []

        players = []
        with self.path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                player = _player_from(row)
                if player is not None:
                    players.append(player)
        return players

    def get(self, email: str) -> Player | None:
        """The player with this address, if they have played before."""
        wanted = normalise_email(email)
        return next((p for p in self.all() if p.email == wanted), None)

    def best_for(self, email: str) -> float | None:
        player = self.get(email)
        return player.best_seconds if player else None

    def record(self, name: str, email: str, seconds: float) -> Player:
        """Save a run, keeping only the better of it and what is already there.

        Returns the stored player, so the caller can tell what their best is
        now without reading the file again.
        """
        stored = {p.email: p for p in self.all()}
        key = normalise_email(email)

        seconds = round(seconds, PRECISION)
        previous = stored.get(key)
        best = seconds if previous is None else min(seconds, previous.best_seconds)
        # A returning player's latest spelling of their own name wins; it is
        # more likely to be the correction than the mistake.
        updated = Player(name=name.strip(), email=key, best_seconds=best)

        stored[key] = updated
        self._write(stored.values())
        return updated

    def top(self, count: int = 10) -> list[Player]:
        """The leaderboard: quickest first."""
        return sorted(self.all(), key=lambda p: p.best_seconds)[:count]

    def _write(self, players) -> None:
        """Replace the file in one step.

        Written to a temporary file alongside and moved into place, so a crash
        or a pulled power lead mid-save leaves the previous file intact rather
        than a half-written one. `os.replace` is atomic within a directory.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)

        handle = tempfile.NamedTemporaryFile(
            "w", newline="", encoding="utf-8",
            dir=self.path.parent, prefix=".players-", suffix=".tmp", delete=False,
        )
        try:
            with handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                for player in players:
                    writer.writerow({
                        "name": player.name,
                        "email": player.email,
                        "best_seconds": f"{player.best_seconds:.{PRECISION}f}",
                    })
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise


def _player_from(row: dict[str, str]) -> Player | None:
    email = normalise_email(row.get("email") or "")
    if not email:
        return None
    try:
        best = float(row.get("best_seconds") or "")
    except ValueError:
        return None
    return Player(name=(row.get("name") or "").strip(), email=email, best_seconds=best)
