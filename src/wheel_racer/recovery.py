"""Noticing when a player is stuck, and deciding to put them back on track.

Off-track is meant to be a time penalty, not a dead end. But a first-timer who
loses the track entirely can end up crawling across the infield or driving away
from the circuit, and there is no mechanic in this game to save them: there is
no reverse, no brake, and the auto-throttle keeps them going wherever they are
pointed. Someone stranded in a corner of the screen with a queue watching is the
exact failure this booth game is built to avoid.

So a watchdog. Two ways to qualify as lost, both needing to persist — a moment
of either is just normal racing:

  * a long time on the grass, which means they cannot find the tarmac
  * a long time pointing backwards, which means they will not find it soon

The delays are what keep this from firing on a spirited excursion. Clipping the
grass through a corner is a fraction of a second, and a bit of oversteer points
the car across the track but never up it.
"""

from __future__ import annotations

import math


class RecoveryMonitor:
    """Watches for a lost car and asks for a respawn once it is sure."""

    def __init__(self, off_track_seconds: float, backwards_seconds: float) -> None:
        self.off_track_seconds = off_track_seconds
        self.backwards_seconds = backwards_seconds
        self._off_track_for = 0.0
        self._backwards_for = 0.0

    @property
    def off_track_for(self) -> float:
        """Unbroken time on the grass, for the HUD's warning."""
        return self._off_track_for

    @property
    def backwards_for(self) -> float:
        """Unbroken time facing the wrong way."""
        return self._backwards_for

    def reset(self) -> None:
        """Clear both timers. Call on a respawn, and at the start of a run."""
        self._off_track_for = 0.0
        self._backwards_for = 0.0

    def update(self, on_track: bool, car_heading: float, track_heading: float, dt: float) -> bool:
        """Advance one frame. Returns ``True`` on the frame a respawn is due.

        The monitor clears itself when it fires, so a caller that respawns on a
        ``True`` never gets a second one on the next frame.
        """
        self._off_track_for = 0.0 if on_track else self._off_track_for + dt

        # Positive means the car and the circuit point the same way. Being
        # sideways is fine and common; only the far side of sideways counts.
        alignment = math.cos(car_heading - track_heading)
        self._backwards_for = self._backwards_for + dt if alignment < 0.0 else 0.0

        if (
            self._off_track_for >= self.off_track_seconds
            or self._backwards_for >= self.backwards_seconds
        ):
            self.reset()
            return True
        return False
