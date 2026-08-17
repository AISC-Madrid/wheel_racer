"""Test setup.

Rendering tests run against SDL's dummy video driver so they need no display,
which keeps them working over SSH and in CI.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
