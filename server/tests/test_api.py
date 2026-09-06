"""The HTTP surface: what gets in, what gets refused, and what leaks.

The cases that matter most here are the negative ones. This is the half of the
project that faces the internet, and every test below that expects a 401, a 422
or an empty field is describing something that must not be possible from a
browser.
"""

from __future__ import annotations

import pytest

from conftest import run_payload


# --- reading is open ---------------------------------------------------------

def test_the_board_needs_no_credentials(client):
    response = client.get("/api/board")
    assert response.status_code == 200
    assert response.json()["standings"] == []


def test_the_board_reports_what_the_booth_filed(client, booth):
    client.post("/api/runs", json=run_payload(), headers=booth)
    body = client.get("/api/board").json()
    assert body["standings"][0]["name"] == "Marta"
    assert body["standings"][0]["seconds"] == 14.88
    assert body["players"] == 1


def test_the_board_never_carries_an_email(client, booth):
    client.post("/api/runs", json=run_payload(), headers=booth)
    assert "example.com" not in client.get("/api/board").text


def test_the_board_never_carries_a_surname(client, booth):
    client.post("/api/runs", json=run_payload(name="Marta Ruiz"), headers=booth)
    assert "Ruiz" not in client.get("/api/board").text


def test_a_huge_limit_is_clamped_not_honoured(client):
    """The one endpoint the whole room can call must not take instructions
    about how much work to do."""
    assert client.get("/api/board", params={"limit": 10_000_000}).status_code == 200


def test_health_says_whether_the_tokens_are_there(client):
    body = client.get("/health").json()
    assert body["ok"] and body["configured"]
    assert body["terms_url"].startswith("https://")


# --- writing is not ----------------------------------------------------------

def test_a_run_without_a_token_is_refused(client):
    assert client.post("/api/runs", json=run_payload()).status_code == 401


def test_a_run_with_the_wrong_token_is_refused(client):
    response = client.post("/api/runs", json=run_payload(),
                           headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_the_admin_token_is_not_the_booth_token(client, admin):
    assert client.post("/api/runs", json=run_payload(), headers=admin).status_code == 401


def test_the_booth_token_cannot_read_the_mailing_list(client, booth):
    assert client.get("/api/admin/players.csv", headers=booth).status_code == 401


def test_a_server_with_no_tokens_accepts_nothing(client, monkeypatch):
    from wheel_racer_server.config import settings

    monkeypatch.delenv("WHEEL_RACER_BOOTH_TOKEN")
    settings.cache_clear()
    response = client.post("/api/runs", json=run_payload(),
                           headers={"Authorization": "Bearer anything"})
    # 503, not 401: nothing the caller could send would work, and the fault is
    # the deployment's.
    assert response.status_code == 503


def test_an_unconfigured_server_still_serves_the_board(client, monkeypatch):
    from wheel_racer_server.config import settings

    monkeypatch.delenv("WHEEL_RACER_BOOTH_TOKEN")
    settings.cache_clear()
    assert client.get("/api/board").status_code == 200
    assert client.get("/health").json()["configured"] is False


# --- what a valid run looks like ---------------------------------------------

def test_a_run_comes_back_with_its_standing(client, booth):
    body = client.post("/api/runs", json=run_payload(), headers=booth).json()
    assert body == {"duplicate": False, "best_seconds": 14.88, "previous_best": None,
                    "improved": True, "position": 1, "players": 1}


def test_the_same_run_id_twice_is_accepted_and_ignored(client, booth):
    client.post("/api/runs", json=run_payload(), headers=booth)
    body = client.post("/api/runs", json=run_payload(), headers=booth).json()
    assert body["duplicate"] and not body["improved"]
    assert client.get("/api/board").json()["runs"] == 1


@pytest.mark.parametrize("seconds", [0.01, 1.0, 4.9, 601.0, 9_999_999.0])
def test_an_implausible_lap_is_refused(client, booth, seconds):
    response = client.post("/api/runs", json=run_payload(seconds=seconds),
                           headers=booth)
    assert response.status_code == 422


@pytest.mark.parametrize("seconds", [-1.0, 0.0])
def test_a_lap_that_is_not_a_duration_is_refused(client, booth, seconds):
    assert client.post("/api/runs", json=run_payload(seconds=seconds),
                       headers=booth).status_code == 422


@pytest.mark.parametrize("email", ["", "marta", "marta@", "@example.com",
                                   "marta example@x.com", "a@b"])
def test_an_address_that_cannot_be_one_is_refused(client, booth, email):
    assert client.post("/api/runs", json=run_payload(email=email),
                       headers=booth).status_code == 422


def test_an_unusual_but_real_address_gets_in(client, booth):
    """The laptop validated this in front of the person who typed it. The
    server re-deciding what an address looks like is how a release starts
    rejecting people at a stand."""
    response = client.post(
        "/api/runs",
        json=run_payload(email="marta+aisc@sub.dominio-raro.museum"),
        headers=booth,
    )
    assert response.status_code == 200


@pytest.mark.parametrize("missing", ["id", "name", "email", "seconds", "station",
                                     "raced_at", "terms_version",
                                     "terms_accepted_at"])
def test_every_field_is_required(client, booth, missing):
    payload = run_payload()
    del payload[missing]
    assert client.post("/api/runs", json=payload, headers=booth).status_code == 422


def test_a_name_of_only_spaces_is_refused(client, booth):
    assert client.post("/api/runs", json=run_payload(name="   "),
                       headers=booth).status_code == 422


# --- sign-in -----------------------------------------------------------------

def test_lookup_finds_a_returning_player(client, booth):
    client.post("/api/runs", json=run_payload(), headers=booth)
    body = client.post("/api/players/lookup", json={"email": "MARTA@example.com"},
                       headers=booth).json()
    assert body == {"name": "Marta Ruiz", "best_seconds": 14.88,
                    "runs": 1, "position": 1}


def test_lookup_of_someone_new_is_a_plain_404(client, booth):
    response = client.post("/api/players/lookup", json={"email": "new@example.com"},
                           headers=booth)
    assert response.status_code == 404


def test_lookup_needs_the_booth_token(client):
    assert client.post("/api/players/lookup",
                       json={"email": "marta@example.com"}).status_code == 401


# --- who is driving ----------------------------------------------------------

def test_a_station_shows_up_on_the_board(client, booth):
    client.post("/api/stations", headers=booth, json={
        "station": "booth-1", "state": "racing", "driver": "Marta Ruiz",
        "lap": 1, "laps": 2})
    live = client.get("/api/board").json()["live"]
    assert live == [{"station": "booth-1", "state": "racing", "driver": "Marta",
                     "lap": 1, "laps": 2}]


def test_a_station_never_publishes_a_surname(client, booth):
    client.post("/api/stations", headers=booth, json={
        "station": "booth-1", "state": "racing", "driver": "Marta Ruiz"})
    assert "Ruiz" not in client.get("/api/board").text


def test_a_state_the_board_cannot_draw_is_refused(client, booth):
    assert client.post("/api/stations", headers=booth, json={
        "station": "booth-1", "state": "on fire"}).status_code == 422


def test_packing_up_clears_the_stand(client, booth):
    client.post("/api/stations", headers=booth,
                json={"station": "booth-1", "state": "idle"})
    assert client.delete("/api/stations/booth-1", headers=booth).status_code == 204
    assert client.get("/api/board").json()["live"] == []


def test_a_station_update_needs_the_booth_token(client):
    assert client.post("/api/stations",
                       json={"station": "x", "state": "idle"}).status_code == 401


# --- running the fair --------------------------------------------------------

def test_the_export_is_a_csv_with_the_addresses(client, booth, admin):
    client.post("/api/runs", json=run_payload(), headers=booth)
    response = client.get("/api/admin/players.csv", headers=admin)
    assert response.status_code == 200
    assert "Marta@Example.com" in response.text
    assert "terms_accepted_at" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_the_export_is_never_public(client):
    assert client.get("/api/admin/players.csv").status_code == 401


def test_an_empty_export_is_not_an_error(client, admin):
    assert client.get("/api/admin/players.csv", headers=admin).status_code == 200


def test_hiding_a_name_takes_it_off_the_board(client, booth, admin):
    client.post("/api/runs", json=run_payload(name="Unprintable"), headers=booth)
    response = client.post("/api/admin/players/hide", headers=admin,
                           json={"email": "marta@example.com", "hidden": True})
    assert response.status_code == 204
    assert client.get("/api/board").json()["standings"] == []


def test_hiding_needs_the_admin_token(client, booth):
    assert client.post("/api/admin/players/hide", headers=booth,
                       json={"email": "marta@example.com"}).status_code == 401


def test_hiding_someone_who_is_not_there_says_so(client, admin):
    assert client.post("/api/admin/players/hide", headers=admin,
                       json={"email": "nobody@example.com"}).status_code == 404


def test_erasing_removes_them_from_the_export_too(client, booth, admin):
    client.post("/api/runs", json=run_payload(), headers=booth)
    assert client.post("/api/admin/players/erase", headers=admin,
                       json={"email": "marta@example.com"}).status_code == 204
    assert "Marta" not in client.get("/api/admin/players.csv", headers=admin).text


# --- rate limiting -----------------------------------------------------------

def test_a_flood_of_writes_is_cut_off(client, booth, monkeypatch):
    from wheel_racer_server.config import settings

    monkeypatch.setenv("WHEEL_RACER_WRITES_PER_MINUTE", "5")
    settings.cache_clear()

    codes = [client.post("/api/stations", headers=booth,
                         json={"station": "booth-1", "state": "idle"}).status_code
             for _ in range(8)]
    assert codes.count(429) == 3
    assert codes[0] == 204


def test_the_limit_never_touches_reading(client, monkeypatch):
    """A hall full of phones behind one wifi is one address making hundreds of
    requests. Rate limiting that would lock the stand's own visitors out of the
    thing the stand is advertising."""
    from wheel_racer_server.config import settings

    monkeypatch.setenv("WHEEL_RACER_WRITES_PER_MINUTE", "2")
    settings.cache_clear()
    assert all(client.get("/api/board").status_code == 200 for _ in range(50))


# --- starting the board again ------------------------------------------------

def test_a_reset_clears_the_public_board(client, booth, admin):
    client.post("/api/runs", json=run_payload(), headers=booth)
    response = client.post("/api/admin/reset", headers=admin)
    assert response.status_code == 200
    assert response.json()["players"] == 0
    assert client.get("/api/board").json()["standings"] == []


def test_a_reset_needs_the_admin_token(client, booth):
    assert client.post("/api/admin/reset", headers=booth).status_code == 401
    assert client.post("/api/admin/reset").status_code == 401


def test_a_reset_leaves_the_mailing_list_alone(client, booth, admin):
    client.post("/api/runs", json=run_payload(), headers=booth)
    client.post("/api/admin/reset", headers=admin)
    assert "Marta" in client.get("/api/admin/players.csv", headers=admin).text


def test_a_reset_makes_a_returning_player_new_again(client, booth, admin):
    client.post("/api/runs", json=run_payload(), headers=booth)
    client.post("/api/admin/reset", headers=admin)
    response = client.post("/api/players/lookup", headers=booth,
                           json={"email": "marta@example.com"})
    assert response.status_code == 404


def test_a_reset_can_be_undone_over_http(client, booth, admin):
    client.post("/api/runs", json=run_payload(), headers=booth)
    client.post("/api/admin/reset", headers=admin)
    response = client.delete("/api/admin/reset", headers=admin)
    assert response.json() == {"since": None, "players": 1, "runs": 1}
    assert len(client.get("/api/board").json()["standings"]) == 1


def test_the_undo_needs_the_admin_token(client, booth):
    assert client.delete("/api/admin/reset", headers=booth).status_code == 401


def test_a_reset_can_be_dated(client, booth, admin):
    """The four o'clock realisation that the line should have been at two."""
    client.post("/api/runs", json=run_payload(raced_at="2026-03-14T11:02:03+01:00"),
                headers=booth)
    client.post("/api/admin/reset", headers=admin,
                json={"since": "2026-03-14T09:00:00+00:00"})
    assert len(client.get("/api/board").json()["standings"]) == 1


def test_health_says_where_the_board_counts_from(client, admin):
    assert client.get("/health").json()["board_since"] is None
    client.post("/api/admin/reset", headers=admin)
    assert client.get("/health").json()["board_since"] is not None
