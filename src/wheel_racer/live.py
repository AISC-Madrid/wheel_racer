"""What the game is doing right now, for the second screen to read.

The leaderboard runs as its own process on the other monitor, so it needs some
way to know who is driving and how their lap is going. This is that channel:
one small JSON file the game rewrites a few times a second and the leaderboard
polls.

A file rather than a socket or a pipe, for booth reasons rather than technical
ones. Neither process has to start first, either can be killed and restarted
mid-afternoon without the other noticing, and when something looks wrong you
can `cat` the channel and see exactly what the game thinks is happening. A
socket would give lower latency than anything here needs and take the game down
with the leaderboard.

The important trick is that a running clock is published as **the time it
started**, not as the time it currently reads. The leaderboard subtracts that
from its own clock every frame, so a 60fps lap timer on the second screen costs
two writes a second on this one — and a late or dropped write shows up as
nothing at all, rather than as a clock that stutters.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .atomicfile import write_json

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "live.json"

# How often the game rewrites the file when nothing has changed. This is a
# heartbeat as much as an update: the leaderboard uses the gap since the last
# write to tell "the game is idle" from "the game is gone", and it has to be
# quick enough that a lap clock never visibly lags.
HEARTBEAT_SECONDS = 0.5

# No write for this long means the game is not running. Generous next to the
# heartbeat, because the alternative — a leaderboard that drops out every time
# the game stutters — is worse than one that takes two seconds to notice a real
# shutdown.
STALE_AFTER_SECONDS = 3.0

# How far the start of a running lap may drift before it counts as a different
# lap. Far below anything a person could see on a clock, and far above the
# float noise between two ways of measuring the same second.
CLOCK_TOLERANCE_SECONDS = 0.02


def _near(one: float | None, other: float | None) -> bool:
    """Whether two lap starts are the same instant, allowing for drift."""
    if one is None or other is None:
        return one is other
    return abs(one - other) <= CLOCK_TOLERANCE_SECONDS


@dataclass(frozen=True)
class LiveState:
    """A snapshot of the driver's seat, as the second screen needs it.

    Deliberately no email. This is written to be read by a screen pointed at a
    public stand, and the addresses the booth collects have no business on it.
    """

    state: str = "idle"
    """One of: idle, ready, countdown, racing, result."""

    name: str = ""
    lap: int = 0
    laps: int = 0

    clock_started_at: float | None = None
    """Wall-clock time the running lap began, or None if no lap is running.

    Both processes are on the same machine, so `time.time()` is a shared clock
    and the leaderboard can render the lap timer itself.
    """

    frozen_time: float | None = None
    """A finished time to show instead of a running clock."""

    personal_best: float | None = None
    beat_their_best: bool = False
    updated_at: float = field(default_factory=time.time)

    @property
    def is_driving(self) -> bool:
        return self.state == "racing"

    def elapsed(self, now: float | None = None) -> float | None:
        """What the lap clock reads, worked out here rather than sent."""
        if self.clock_started_at is None:
            return self.frozen_time
        return max(0.0, (time.time() if now is None else now) - self.clock_started_at)

    def is_stale(self, now: float | None = None) -> bool:
        """Whether the game has stopped writing — crashed, closed, or unplugged."""
        elapsed = (time.time() if now is None else now) - self.updated_at
        return elapsed > STALE_AFTER_SECONDS


class LiveChannel:
    """The file, from either end.

    The game holds one and calls `publish`; the leaderboard holds one and calls
    `read`. Nothing stops both, and the tests use both.
    """

    def __init__(self, path: Path | str = DEFAULT_PATH,
                 heartbeat: float = HEARTBEAT_SECONDS) -> None:
        self.path = Path(path)
        self.heartbeat = heartbeat
        self._last_written: LiveState | None = None
        self._last_write_at = 0.0
        self._last_read_at: float | None = None
        self._cached: LiveState | None = None

    # --- writing side --------------------------------------------------------

    def publish(self, state: LiveState, now: float | None = None) -> bool:
        """Write the state, if it has changed or the heartbeat is due.

        Called every frame and throttled here rather than at the call site, so
        the game loop does not have to know anything about how often the other
        screen wants updating. Returns whether it actually wrote.
        """
        now = time.time() if now is None else now
        changed = self._differs(state)
        if not changed and now - self._last_write_at < self.heartbeat:
            return False

        try:
            # Stamped at the moment of writing, not of construction, so a
            # heartbeat re-publish of an unchanged state still counts as fresh.
            self._write(LiveState(**{**asdict(state), "updated_at": now}))
        except OSError:
            # This channel is a courtesy to the other screen and nothing
            # more. The person at the wheel must never find out that it
            # could not be written — losing one update costs half a second
            # of a name on a board, and raising here would end their run.
            #
            # Nothing is recorded as written, so the next frame tries again
            # rather than waiting for the heartbeat.
            return False

        self._last_written = state
        self._last_write_at = now
        return True

    def _differs(self, state: LiveState) -> bool:
        """Whether anything that matters has changed since the last write.

        `updated_at` is excluded — it changes every frame by definition, and
        comparing it would make every state look new and defeat the throttle.

        `clock_started_at` is compared loosely, and that matters more than it
        looks. It is worked out as `time.time() - running`, so it names a fixed
        instant — but the game's accumulated clock and the wall clock drift
        against each other by microseconds every frame, and compared exactly it
        is never the same value twice. That turned the throttle off completely:
        this file was being rewritten sixty times a second instead of two, for
        a number that had not meaningfully moved, which is thirty times as many
        chances to collide with the station reading it.
        """
        if self._last_written is None:
            return True

        mine, theirs = asdict(state), asdict(self._last_written)
        del mine["updated_at"], theirs["updated_at"]

        if _near(mine.pop("clock_started_at"), theirs.pop("clock_started_at")):
            return mine != theirs
        return True

    def _write(self, state: LiveState) -> None:
        """Replace the file in one step.

        The station is polling this file continuously, so it must never
        catch a half-written one — see `atomicfile`, which also waits out
        Windows' habit of refusing to replace a file somebody has open.
        """
        # One attempt and no waiting. This runs inside the game loop, where
        # blocking for even a few milliseconds is a dropped frame — and
        # where the retry that matters is simply the next frame, sixteen
        # milliseconds away, which `publish` arranges by not recording a
        # failed write as written.
        write_json(self.path, asdict(state), prefix=".live-",
                   attempts=1, backoff=0.0)

    def clear(self) -> None:
        """Remove the channel, so a stopped game does not leave a stale driver
        on the other screen for whoever walks past next."""
        self.path.unlink(missing_ok=True)
        self._last_written = None

    # --- reading side --------------------------------------------------------

    def read(self) -> LiveState | None:
        """The latest state, or None if the game is not running.

        Re-read only when the file's timestamp moves. Polling a file sixty
        times a second is fine; parsing it sixty times a second to get the same
        answer is not.

        A file that is missing, half-parsed or written by a newer version comes
        back as None rather than raising. The leaderboard's whole job is to
        keep showing the board, and it can do that perfectly well without
        knowing who is driving — falling over instead would take the stand's
        one eye-catching screen down with it.
        """
        try:
            stamp = self.path.stat().st_mtime
        except OSError:
            self._cached, self._last_read_at = None, None
            return None

        if stamp != self._last_read_at:
            self._last_read_at = stamp
            self._cached = self._parse()
        return self._cached

    def _parse(self) -> LiveState | None:
        try:
            with self.path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None

        # Only the fields this version knows about, so an older leaderboard
        # left running against a newer game degrades to what it understands
        # instead of crashing on a key it has never heard of.
        known = {f for f in LiveState.__dataclass_fields__}
        try:
            return LiveState(**{k: v for k, v in raw.items() if k in known})
        except TypeError:
            return None
