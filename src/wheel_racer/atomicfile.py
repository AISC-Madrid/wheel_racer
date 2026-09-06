"""Writing a small file that a second process is reading at the same time.

Three things in this project are written by one process and read by another
while the fair is running — the live channel, the queue of finished runs, and
the roster of who has played here. All of them do the same dance: write a
temporary file alongside the real one, then move it into place, so a reader
polling the file sees either the old version or the new one and never a
half-written one.

On Linux that is the end of the story: `rename(2)` over a file somebody has
open is fine. On Windows it is not. `os.replace` there fails with
`PermissionError: [WinError 5]` if anything has the destination open at that
instant, which for a file being written twice a second and polled twice a
second is not a rare event — it is a booth crashing twenty seconds into an
afternoon, which is exactly how this was found.

So the move is retried for a few milliseconds. The reader's grip is brief — it
opens the file, parses it and closes it — so one retry is almost always enough,
and the cost when there is no contention is a single successful call.

Retrying is not the same as never failing, and the callers still have to decide
what a failure means. For the live channel and the caches it means "skip this
write, the next one is half a second away". For a finished run it means telling
somebody, because that one cannot be reconstructed.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

# How long to keep trying, by default. Windows holds the destination only for
# as long as the reader takes to parse a few hundred bytes, so this is
# generous by orders of magnitude — and it is spent only in the case that
# would have failed outright before.
#
# It is a default and not a rule, because how long a caller may block depends
# entirely on who is waiting. Sixty milliseconds is nothing once per run and
# is four dropped frames inside a game loop, so the live channel asks for
# almost none of it: it is called sixty times a second, and its retry is the
# next frame rather than this loop.
ATTEMPTS = 6
BACKOFF_SECONDS = 0.01


def write_json(path: Path, payload, prefix: str = ".tmp-",
               attempts: int = ATTEMPTS,
               backoff: float = BACKOFF_SECONDS) -> None:
    """Replace `path` with `payload` as JSON, atomically.

    `attempts` and `backoff` bound how long this may block. A caller with
    somebody waiting on the next frame should ask for one attempt and no
    wait; a caller that runs once per run can afford to be patient.

    Raises `OSError` if the file could not be put in place — which on
    Windows means another process held it open throughout, and everywhere
    else means something is actually wrong with the disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent,
        prefix=prefix, suffix=".tmp", delete=False,
    )
    try:
        with handle:
            json.dump(payload, handle)
        _replace(handle.name, path, attempts, backoff)
    except BaseException:
        # Whatever went wrong, do not leave the temporary file behind. A booth
        # laptop that has been running all afternoon should not end the day
        # with a directory full of `.live-*.tmp`.
        Path(handle.name).unlink(missing_ok=True)
        raise


def _replace(source: str, destination: Path,
             attempts: int, backoff: float) -> None:
    """`os.replace`, with Windows' idea of a busy file waited out."""
    for attempt in range(max(1, attempts)):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            # Windows only: something has the destination open. Nothing is
            # wrong with either file — the reader simply has not let go yet.
            if attempt == max(1, attempts) - 1:
                raise
            if backoff:
                time.sleep(backoff)
