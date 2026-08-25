"""Opening a window, for both of the stand's screens.

The game and the leaderboard have nothing else in common, but they want their
window to behave identically: open as something you can drag onto whichever
monitor it belongs on, resize by pulling a corner, and go fullscreen on the
same key once it is where you want it. Setting the stand up means moving
windows around, and two screens that answered different keys for that would be
one more thing to remember with a queue already forming.
"""

from __future__ import annotations

import signal
from types import FrameType

import pygame

# Smallest window either screen will open. Nothing breaks above this; below it
# the fonts bottom out at their minimum size and the panels start to overlap,
# and at zero width a scale factor would be zero and the maths would divide by
# it.
MIN_WINDOW = (640, 360)

# The fullscreen shortcut. F11 is the convention, but macOS usually eats it
# before any application sees it, so Cmd-F and Ctrl-F work too — and they need
# the modifier precisely because on the game's sign-in screen a bare F belongs
# to whoever is spelling their name.
FULLSCREEN_MODIFIERS = pygame.KMOD_META | pygame.KMOD_CTRL


def is_fullscreen_shortcut(event: pygame.event.Event) -> bool:
    """Whether this key means "change how this window fills the screen"."""
    if event.type != pygame.KEYDOWN:
        return False
    if event.key == pygame.K_F11:
        return True
    return event.key == pygame.K_f and bool(event.mod & FULLSCREEN_MODIFIERS)


def open_display(size: tuple[int, int] | None, fullscreen: bool,
                 display: int = 0) -> pygame.Surface:
    """Open, or reopen, a window.

    Fullscreen deliberately asks for ``(0, 0)``, which SDL reads as "whatever
    the desktop is already at". Naming a size instead puts the display through
    a mode change to something smaller and stretches it back up, which on a
    booth laptop is a soft, faintly blurry picture. Both screens scale
    themselves to any window, so they would rather have the real pixels.

    Windowed mode is resizable, because the stand's monitors are not known in
    advance and dragging a corner is a faster way to find the size that suits
    one than restarting with different numbers.

    `display` picks a monitor to open *on*. It is a starting position and
    nothing more — the window can be dragged anywhere afterwards, which is what
    actually happens when a stand is being set up and nobody is sure yet which
    screen the operating system decided was number one.
    """
    flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
    if fullscreen:
        wanted = (0, 0)
    else:
        width, height = size or MIN_WINDOW
        wanted = (max(width, MIN_WINDOW[0]), max(height, MIN_WINDOW[1]))

    try:
        return pygame.display.set_mode(wanted, flags, display=display)
    except pygame.error:
        # The monitor was unplugged, or the numbering is not what we assumed.
        # The stand is being set up in a hurry either way, so open somewhere
        # rather than refusing to open at all.
        return pygame.display.set_mode(wanted, flags)


def quit_on_signals() -> None:
    """Turn Ctrl-C and `kill` into an ordinary window-close.

    SDL installs handlers of its own for both signals, and on macOS the result
    is that neither reaches Python *and* nothing reaches the event queue
    either: the process simply carries on running. At a stand somebody will
    always reach for Ctrl-C in the terminal before they think to click the
    close button, and a game that ignores it is one that has to be hunted down
    with `kill` while a queue waits.

    So both signals are routed back to the one shutdown path everything else
    already uses — the same QUIT the close button sends — which means the
    camera is released, the live channel is cleared and the second screen is
    closed, exactly as they are on a normal exit.

    Must be called *after* `pygame.init()`, which is where SDL installs the
    handlers this replaces.
    """
    def request_quit(number: int, frame: FrameType | None) -> None:
        # Handled once, then handed back. A second Ctrl-C on a wedged loop
        # should still kill the process outright rather than being politely
        # swallowed by a handler that is evidently not being listened to.
        signal.signal(number, signal.SIG_DFL)
        pygame.event.post(pygame.event.Event(pygame.QUIT))

    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, request_quit)
