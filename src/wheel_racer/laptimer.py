"""Lap timing with ordered-checkpoint validation.

A leaderboard is only fun if the times on it are real, and the cheap way to
fake a time is to cut across the infield. So a lap only counts if the car
passed through every checkpoint, in order, driving forwards, without ever
jumping.

That last condition does most of the work. Progress is the arc length of the
*nearest point on the centreline*, so a car cutting across the middle of the
circuit does not slide smoothly forward through the checkpoints — the nearest
centreline point leaps from one side of the infield to the other, and the jump
is unmistakable. Rejecting discontinuous progress therefore rejects shortcuts,
without needing any shortcut-detection geometry.
"""

from __future__ import annotations


class LapTimer:
    """Times one lap at a time and decides whether it was driven honestly.

    Call :meth:`start` when the lights go green, then :meth:`update` every
    frame with the car's current progress. It returns the lap time on the frame
    a valid lap completes, and ``None`` on every other frame.
    """

    def __init__(self, num_checkpoints: int = 4, max_progress_step: float = 0.05) -> None:
        if num_checkpoints < 1:
            raise ValueError("a lap needs at least one checkpoint")
        if not 0.0 < max_progress_step < 0.5:
            raise ValueError("max_progress_step must be between 0 and 0.5")

        self.num_checkpoints = num_checkpoints
        self.max_progress_step = max_progress_step
        self.best: float | None = None
        self.last: float | None = None
        self._lap_start: float | None = None
        self._next_checkpoint = 1
        self._last_progress: float | None = None

    @property
    def is_running(self) -> bool:
        """Whether a lap is currently being timed."""
        return self._lap_start is not None

    @property
    def checkpoints_required(self) -> int:
        """How many gates a lap must clear.

        One fewer than ``num_checkpoints``, because the checkpoint at progress
        zero is the start line itself and crossing it is what ends the lap.
        """
        return self.num_checkpoints - 1

    @property
    def checkpoints_reached(self) -> int:
        """Gates cleared so far this lap, for the HUD's "2/3"."""
        return self._next_checkpoint - 1

    @property
    def checkpoint_progresses(self) -> list[float]:
        """Where the gates sit around the lap, for the renderer to draw them."""
        return [i / self.num_checkpoints for i in range(self.num_checkpoints)]

    def reset(self) -> None:
        """Forget everything, including the best time. Call for a new player."""
        self.best = None
        self.last = None
        self._lap_start = None
        self._next_checkpoint = 1
        self._last_progress = None

    def start(self, now: float) -> None:
        """Begin timing a lap.

        The car starts sitting on the line rather than arriving at it, so the
        first lap has to be started explicitly — there is no crossing to detect.
        """
        self._lap_start = now
        self._next_checkpoint = 1
        self._last_progress = None

    def current_time(self, now: float) -> float | None:
        """Elapsed time on the lap in progress, or ``None`` if none is running."""
        if self._lap_start is None:
            return None
        return now - self._lap_start

    def update(self, progress: float, now: float) -> float | None:
        """Advance one frame. Returns a lap time only when a valid lap ends."""
        previous = self._last_progress
        self._last_progress = progress

        if previous is None or self._lap_start is None:
            return None

        step = _forward_step(previous, progress)
        if not 0.0 <= step <= self.max_progress_step:
            # Either going backwards, or a jump too big to have been driven —
            # the signature of a cut across the infield. Credit nothing.
            return None

        # Unwrapped position, which may run past 1.0 on the finishing frame.
        position = previous + step
        self._credit_checkpoint(previous, position)

        if position >= 1.0:
            return self._finish(now)
        return None

    def _credit_checkpoint(self, previous: float, position: float) -> None:
        """Tick off the next gate if this frame drove through it.

        Only ever the *next* one, and only ever forwards, so no single frame
        can account for more of the lap than it actually covered.
        """
        if self._next_checkpoint >= self.num_checkpoints:
            return
        threshold = self._next_checkpoint / self.num_checkpoints
        if previous < threshold <= position:
            self._next_checkpoint += 1

    def _finish(self, now: float) -> float | None:
        """Handle a forward crossing of the start line."""
        if self._next_checkpoint < self.num_checkpoints:
            # Gates were missed, so this is not a lap. Start a fresh one from
            # here rather than stranding the player with a dead clock.
            self.start(now)
            return None

        lap = now - self._lap_start
        self._lap_start = None
        self.last = lap
        if self.best is None or lap < self.best:
            self.best = lap
        return lap


def _forward_step(previous: float, progress: float) -> float:
    """Signed change in progress, resolved across the start line.

    Both values are in [0, 1), so a step is ambiguous until we assume the car
    moved less than half a lap in one frame — which at 60fps it did.
    """
    step = progress - previous
    if step < -0.5:
        return step + 1.0
    if step > 0.5:
        return step - 1.0
    return step
