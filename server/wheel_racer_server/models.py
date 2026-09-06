"""The wire contract, in one file.

Everything the booth sends and everything the board reads is described here,
so "what does the API accept" is answerable by reading one module rather than
by tracing endpoints. The laptop side does not import this — it has no
pydantic and building the game must not require the server's dependencies — so
this file is also the specification that `wheel_racer.station` is written
against. Keep the two honest with each other: a field renamed here is a field
renamed there.

Validation here is about shape and plausibility, never about taste. A name
with odd punctuation in it is somebody's name; a lap time of 0.01 seconds is
somebody's stolen token.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# What a booth can say it is doing. Spelled out rather than left free-form, so
# the board never has to render a state it has no design for.
StationState = Literal["idle", "ready", "countdown", "racing", "result"]

NAME_MAX = 64
EMAIL_MAX = 254
STATION_MAX = 32


class Submission(BaseModel):
    """One finished run, as the booth reports it.

    The id is minted on the laptop, not here. That is what makes the whole
    offline queue safe: a run that was written, lost its reply to a dropped
    connection and got retried arrives twice with the same id, and the second
    one changes nothing.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(min_length=8, max_length=64)
    name: str = Field(min_length=1, max_length=NAME_MAX)
    email: str = Field(min_length=3, max_length=EMAIL_MAX)
    seconds: float = Field(gt=0)
    station: str = Field(min_length=1, max_length=STATION_MAX)
    raced_at: datetime
    terms_version: str = Field(min_length=1, max_length=32)
    terms_accepted_at: datetime

    @field_validator("email")
    @classmethod
    def _plausible_address(cls, value: str) -> str:
        """The coarsest possible check, on purpose.

        The real validation happens on the laptop, in front of the person who
        typed it and can fix it. Re-deciding here what counts as an address
        would mean a server release could start rejecting people at a booth,
        which is the worst possible place to discover a disagreement about
        email syntax.
        """
        if value.count("@") != 1 or any(c.isspace() for c in value):
            raise ValueError("not an email address")
        local, _, domain = value.partition("@")
        if not local or "." not in domain:
            raise ValueError("not an email address")
        return value


class Receipt(BaseModel):
    """What the booth gets back, and what it puts on screen.

    `improved` is computed here rather than left to the laptop because the
    laptop cannot know it: another station, or the same player at an earlier
    event, may already hold a better time. The server is the only thing that
    sees every run.
    """

    duplicate: bool
    """This run id had already been recorded. Everything below still holds."""

    best_seconds: float
    previous_best: float | None
    improved: bool
    position: int | None
    players: int


class Lookup(BaseModel):
    """Asking who this address is, at sign-in.

    A POST with a body rather than a GET with the address in the path, so no
    email address is ever written to an access log or a proxy's history.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=EMAIL_MAX)


class PlayerSummary(BaseModel):
    """What is known about one player, for the screen they are standing at."""

    name: str
    best_seconds: float | None
    runs: int
    position: int | None


class StationUpdate(BaseModel):
    """Who is at this booth's wheel right now.

    No clock and no email. The running time stays on the booth's own screen,
    where it is read by the person driving; putting it on the web would mean
    every phone in the room needed its clock agreeing with a laptop's, to
    animate a number nobody outside the stand is watching.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    station: str = Field(min_length=1, max_length=STATION_MAX)
    state: StationState
    driver: str = Field(default="", max_length=NAME_MAX)
    lap: int = Field(default=0, ge=0)
    laps: int = Field(default=0, ge=0)


class Standing(BaseModel):
    """One row of the public board."""

    position: int
    name: str
    """First name only, disambiguated if two people share one. Never an email."""

    seconds: float
    gap: float
    """How far off the leader, in seconds. Zero for the leader itself.

    Sent rather than computed in the browser because it is the whole point of
    a timing tower: `+0.67` is a story, `14.88` is a number.
    """


class Driver(BaseModel):
    """A booth that is currently doing something worth showing."""

    station: str
    state: StationState
    driver: str
    lap: int
    laps: int


class Board(BaseModel):
    """The entire public page, in one response.

    One request rather than three, because the page is opened from a QR code
    on a stand, over whatever mobile signal a conference hall has, and every
    round trip is another chance for the person to give up and walk off.
    """

    standings: list[Standing]
    live: list[Driver]
    players: int
    runs: int
    generated_at: datetime


class Health(BaseModel):
    """Enough to tell "up" from "up but deployed wrong"."""

    ok: bool
    configured: bool
    players: int
    runs: int
    terms_url: str
    terms_version: str


class Moderation(BaseModel):
    """Take a name off the board, or put it back."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=EMAIL_MAX)
    hidden: bool = True


class Erasure(BaseModel):
    """Delete a player and everything they ever drove."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=EMAIL_MAX)
