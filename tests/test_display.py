"""Tests for opening the stand's windows.

Both screens share this, and most of what is worth pinning is what happens when
the stand is not set up the way the code hoped: a monitor that is not plugged
in, a window dragged smaller than anything can be drawn in, and a Ctrl-C that
somebody in a hurry types into the terminal instead of clicking a close button.
"""

import os
import signal

import pygame
import pytest

from wheel_racer.display import (MIN_WINDOW, is_fullscreen_shortcut,
                                 open_display, quit_on_signals)


@pytest.fixture(autouse=True, scope="module")
def display():
    pygame.init()
    yield
    pygame.quit()


class TestOpeningAWindow:
    def test_a_window_can_be_resized(self):
        """The stand's monitors are not known in advance, so dragging a corner
        has to be a way of finding the size that suits one."""
        screen = open_display((1024, 640), fullscreen=False)
        assert screen.get_flags() & pygame.RESIZABLE

    def test_a_tiny_window_is_clamped(self):
        """Below the minimum the fonts bottom out and the panels overlap; at
        zero width a scale factor would be zero and the maths would divide by
        it."""
        screen = open_display((120, 80), fullscreen=False)
        assert screen.get_size() == MIN_WINDOW

    def test_no_size_given_opens_at_the_minimum(self):
        assert open_display(None, fullscreen=False).get_size() == MIN_WINDOW

    def test_fullscreen_takes_the_whole_display(self):
        screen = open_display(None, fullscreen=True)
        assert screen.get_flags() & pygame.FULLSCREEN

    def test_a_monitor_that_is_not_there_still_opens_something(self):
        """Somebody is holding an HDMI cable and a queue is forming. Opening on
        the wrong screen beats refusing to open.

        Which screen it lands on is the driver's business — and under SDL's
        dummy driver there is only one — so what is checked here is the part
        that matters: that asking for a monitor that is not there comes back
        with a window instead of an exception.
        """
        screen = open_display((800, 600), fullscreen=False, display=99)
        assert screen.get_width() > 0 and screen.get_height() > 0


class TestTheFullscreenShortcut:
    def test_f11_is_the_shortcut(self):
        assert is_fullscreen_shortcut(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, mod=0))

    def test_a_bare_f_is_not(self):
        """macOS usually eats F11, so Cmd-F is there as well — but a bare F
        belongs to whoever is spelling their name into the sign-in form."""
        assert not is_fullscreen_shortcut(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_f, unicode="f", mod=0))

    def test_a_held_modifier_makes_it_one(self):
        for modifier in (pygame.KMOD_LMETA, pygame.KMOD_LCTRL):
            assert is_fullscreen_shortcut(pygame.event.Event(
                pygame.KEYDOWN, key=pygame.K_f, unicode="f", mod=modifier))

    def test_releasing_a_key_is_not_a_shortcut(self):
        assert not is_fullscreen_shortcut(
            pygame.event.Event(pygame.KEYUP, key=pygame.K_F11, mod=0))


class TestSignals:
    """Ctrl-C has to work.

    SDL installs handlers of its own, and the result on macOS is that a Ctrl-C
    reaches neither Python nor the event queue — the process just carries on.
    At a stand that means a game which has to be hunted down with `kill`.
    """

    @pytest.fixture(autouse=True)
    def restore_handlers(self):
        """The handler hands itself back to the default once it has fired, so
        without this a later signal would take the test run down with it."""
        saved = {number: signal.getsignal(number)
                 for number in (signal.SIGINT, signal.SIGTERM)}
        yield
        for number, handler in saved.items():
            signal.signal(number, handler)

    @pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
    def test_a_signal_asks_the_window_to_close(self, number):
        pygame.event.clear()
        quit_on_signals()

        os.kill(os.getpid(), number)
        # Python runs the handler between bytecodes rather than immediately, so
        # give the interpreter something to run before looking for the event.
        for _ in range(100):
            pass
        assert any(event.type == pygame.QUIT for event in pygame.event.get())

    def test_a_second_signal_is_not_swallowed(self, monkeypatch):
        """One Ctrl-C asks nicely. A second, on a loop that evidently is not
        listening, has to kill the process outright."""
        quit_on_signals()
        assert signal.getsignal(signal.SIGINT) is not signal.SIG_DFL

        os.kill(os.getpid(), signal.SIGINT)
        for _ in range(100):
            pass
        assert signal.getsignal(signal.SIGINT) is signal.SIG_DFL
