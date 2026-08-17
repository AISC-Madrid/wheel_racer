"""The club logo, loaded once and scaled for the trackside board.

Kept apart from `trackart` for one reason: the logo is an external file, and the
game has to be fine without it. A missing image must never be the thing that
stops the booth running, so every failure here comes back as ``None`` and the
board falls back to plain text.

The PNG is used in preference to the SVG next to it deliberately. pygame will
load an SVG, but through nanosvg, which silently ignores `<pattern>` fills — and
this logo is a pattern fill, so the SVG renders as the caption alone with the
mark missing. Rendering it properly would mean cairo, a system library, on a
laptop that has to work at a table outdoors.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pygame

LOGO_PATH = Path(__file__).resolve().parents[2] / "assets" / "aisc.png"

_cache: dict[tuple[str, int], pygame.Surface | None] = {}
_warned = False


def load_logo(height: int, path: Path | None = None) -> pygame.Surface | None:
    """The logo scaled to `height` pixels tall, or ``None`` if unavailable.

    Cached per size, because the track layer is built once but the game may be
    restarted at a different resolution within a session.
    """
    source = Path(path) if path is not None else LOGO_PATH
    key = (str(source), height)
    if key not in _cache:
        _cache[key] = _load(source, height)
    return _cache[key]


def _load(source: Path, height: int) -> pygame.Surface | None:
    if height <= 0 or not source.is_file():
        _warn_once(f"No club logo at {source} — nothing will be drawn in its place.")
        return None

    try:
        image = _with_alpha(pygame.image.load(str(source)))
    except pygame.error as error:
        _warn_once(f"Could not read the club logo at {source}: {error}")
        return None

    if pygame.surfarray.pixels_alpha(image).min() == 255:
        _warn_once(
            f"The club logo at {source} has no transparent pixels, so it will "
            "appear as a solid rectangle on the grass. Re-export it with a "
            "transparent background."
        )

    width = max(1, round(image.get_width() * height / image.get_height()))
    return _with_alpha(pygame.transform.smoothscale(image, (width, height)))


def _with_alpha(image: pygame.Surface) -> pygame.Surface:
    """Copy onto a surface that definitely has a per-pixel alpha channel.

    Deliberately not `convert_alpha`, which converts to whatever format the
    *display* is using. Where that format carries no alpha, the transparent
    background of the logo comes back opaque and the mark lands on the grass as
    a solid black square. Asking for 32 bits explicitly does not care what the
    display is doing, and this runs once at startup so the cost is nothing.
    """
    if image.get_bitsize() == 32 and image.get_flags() & pygame.SRCALPHA:
        return image

    out = pygame.Surface(image.get_size(), pygame.SRCALPHA, 32)
    out.fill((0, 0, 0, 0))
    out.blit(image, (0, 0))
    return out


def _warn_once(message: str) -> None:
    """Say it, but only the first time — this is called from a draw path."""
    global _warned
    if not _warned:
        _warned = True
        print(message, file=sys.stderr)
