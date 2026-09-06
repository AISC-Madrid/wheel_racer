"""The page itself: that it is served, and that it cannot leak.

Not a test of what it looks like — nothing here can tell whether the board is
legible from four metres, and pretending otherwise would be worse than not
testing it. What these do check is the handful of properties that would be
found out at a stand rather than in a browser: that the page is one request,
that it is served at all, and that nothing personal is baked into it.
"""

from __future__ import annotations

from wheel_racer_server.main import ASSETS, WEB_ROOT


def test_the_page_is_served_at_the_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Hand-Wheel Racer" in response.text


def test_the_page_may_be_cached_but_must_be_revalidated(client):
    """A kiosk left running all afternoon has to pick up a fix without anybody
    walking over to it, and a phone on a slow hall wifi should not refetch the
    whole page on every glance."""
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_the_logo_is_served_from_where_the_game_keeps_it(client):
    assert client.get("/assets/aisc.png").status_code == 200


def test_the_page_is_one_request():
    """Opened from a QR code on a stand, over a conference hall's mobile
    signal. Every extra round trip is another chance for somebody to give up
    and walk off, so the CSS and the script are inline and the only other file
    is the logo."""
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    external = [line for line in page.splitlines()
                if ("<script" in line and "src=" in line)
                or ("<link" in line and "stylesheet" in line)]
    assert external == []


def test_the_page_carries_no_data_of_its_own():
    """Everything on screen arrives from `/api/board` at runtime. A name baked
    into the file is a name that survives a deploy, and this file is public."""
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    assert "@" not in page.replace("@media", "").replace("@keyframes", "")


def test_the_page_and_the_tower_agree_about_black():
    """The stand may show both during a changeover, and two slightly different
    blacks next to each other read as a fault rather than as a design."""
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    assert "#0e1016" in page  # BACKDROP, from leaderboard.py


def test_the_assets_directory_is_the_game_s_own():
    assert (ASSETS / "aisc.png").is_file()
