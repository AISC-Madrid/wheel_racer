"""Tests for how the booth is told where to send things.

Small, but worth pinning: the failure these prevent is somebody at a fair
editing `.env`, restarting, and finding that a variable left over in their
shell is still winning — which looks exactly like the server being down.
"""

import pytest

import wheel_racer.station.config as module


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    """Each test gets its own environment and its own `.env`."""
    for name in ("WHEEL_RACER_SERVER", "WHEEL_RACER_BOOTH_TOKEN",
                 "WHEEL_RACER_STATION", "WHEEL_RACER_KIOSK_PORT",
                 "WHEEL_RACER_BOARD_REFRESH_S", "WHEEL_RACER_HEARTBEAT_S"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / ".env")
    module.settings.cache_clear()
    yield tmp_path / ".env"
    module.settings.cache_clear()


def test_nothing_configured_is_a_supported_state():
    """The stand can be set up and played before anybody has decided what the
    URL is. Results queue on disk until it exists."""
    assert not module.settings().configured


def test_the_environment_is_read():
    import os
    os.environ["WHEEL_RACER_SERVER"] = "https://racer.example.com"
    os.environ["WHEEL_RACER_BOOTH_TOKEN"] = "secret"
    assert module.settings().configured
    assert module.settings().server == "https://racer.example.com"


def test_an_env_file_is_read(fresh):
    fresh.write_text("WHEEL_RACER_SERVER=https://racer.example.com\n"
                     "WHEEL_RACER_BOOTH_TOKEN=secret\n", encoding="utf-8")
    assert module.settings().configured


def test_a_real_variable_beats_the_file(fresh, monkeypatch):
    fresh.write_text("WHEEL_RACER_SERVER=https://from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("WHEEL_RACER_SERVER", "https://from-the-shell")
    assert module.settings().server == "https://from-the-shell"


def test_comments_and_blank_lines_are_ignored(fresh):
    fresh.write_text("# the fair's server\n\n"
                     "WHEEL_RACER_SERVER=https://racer.example.com\n",
                     encoding="utf-8")
    assert module.settings().server == "https://racer.example.com"


def test_quotes_around_a_value_are_stripped(fresh):
    fresh.write_text('WHEEL_RACER_BOOTH_TOKEN="secret"\n', encoding="utf-8")
    assert module.settings().token == "secret"


def test_a_missing_env_file_is_fine():
    assert module.settings().station


def test_the_station_names_itself_after_the_machine():
    """With one stand nobody ever sees this. With two, they tell themselves
    apart without anyone configuring anything."""
    assert module.settings().station
    assert "." not in module.settings().station


def test_a_nonsense_number_falls_back_rather_than_crashing(monkeypatch):
    """A typo in a config file must not stop a booth from opening."""
    monkeypatch.setenv("WHEEL_RACER_KIOSK_PORT", "eight thousand")
    assert module.settings().port == module.DEFAULT_PORT
