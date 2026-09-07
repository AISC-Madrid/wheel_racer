"""The sign-in form: two fields, and the rules about what goes in them.

State and keystrokes only — no drawing. `render.draw_login` decides what it
looks like, which keeps the awkward part (what counts as an email, what happens
when you hit tab) testable without a window.

The whole thing is keyboard-driven on purpose. A booth laptop is usually on a
table with the lid open and no room for a mouse, and the player is about to put
both hands on a bar anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pygame

from . import config

NAME_LIMIT = 22
EMAIL_LIMIT = 42


@dataclass
class Field:
    """One labelled box."""

    label: str
    limit: int
    value: str = ""

    caret = True
    """Whether a blinking caret is drawn while this box has the focus."""

    @property
    def filled(self) -> bool:
        return bool(self.value)

    def type(self, character: str) -> None:
        if len(self.value) < self.limit:
            self.value += character

    def backspace(self) -> None:
        self.value = self.value[:-1]


@dataclass
class Consent:
    """The box that has to be ticked before anybody drives.

    The booth keeps names and email addresses and sends them to a server, so
    there is something to agree to, and it has to be agreed to on purpose.
    Which is why this is a third field rather than a line of small print
    under the panel: small print is not consent, and a queue of students
    will walk past anything that can be walked past.

    It is deliberately awkward to tick by accident. ENTER — the key everyone
    is already pressing to get through the form — does not tick it; only the
    space bar does. ENTER on an unticked box fails the form and says why,
    which costs one keystroke and makes agreeing a decision rather than a
    reflex.
    """

    label: str
    checked: bool = False
    accepted_at: datetime | None = None
    """When it was ticked. Sent with every run, so that consent is
    answerable later rather than merely asserted."""

    caret = False

    @property
    def filled(self) -> bool:
        return self.checked

    @property
    def value(self) -> str:
        """What the box says, drawn the same way a typed field is.

        A mark in the panel's own type rather than a widget: the font is
        monospaced so it lines up under the field above it, and there is no
        glyph here that a system font might turn into an empty square on the
        one screen every player sees.
        """
        return f"[{'x' if self.checked else ' '}]  I accept the terms"

    def toggle(self) -> None:
        self.checked = not self.checked
        # Stamped on the way in rather than read at submission time, so what
        # gets recorded is the moment somebody agreed and not the moment they
        # finished typing their address.
        self.accepted_at = datetime.now(timezone.utc) if self.checked else None


def consent_error(agreed: bool) -> str | None:
    """Whether this run may be recorded at all.

    Phrased as what the tick is for. "You must accept the terms" tells
    somebody they are being made to do something; saying what it allows tells
    them what they are agreeing to, in the two seconds they will spend on it.
    """
    if not agreed:
        return "SPACE to accept — it is how your time gets on the board"
    return None


def name_error(value: str) -> str | None:
    """Whether this will do as a name, and why not if it will not."""
    if not value.strip():
        return "your name, so the leaderboard knows who you are"
    return None


# Generous limits from the email standard. Nobody at a booth will reach them,
# but a paste gone wrong can, and a 900-character "address" in the file helps
# no one.
EMAIL_MAX = 254
LOCAL_MAX = 64
LABEL_MAX = 63

# Punctuation that is legal in the part before the @, beyond letters and digits.
LOCAL_PUNCTUATION = "!#$%&'*+/=?^_`{|}~.-"


def _is_allowed(character: str, punctuation: str) -> bool:
    """Letters and digits in any alphabet, plus the listed punctuation.

    `isalnum` rather than an ASCII whitelist on purpose: it accepts accented
    letters, which real Spanish addresses contain, while still rejecting the
    quotes, brackets and commas that are what a typo actually looks like.
    """
    return character.isalnum() or character in punctuation


def email_error(value: str) -> str | None:
    """Why this will not do as an address, or None if it will.

    Checked in the order a person reads an address, so the message names the
    first thing actually wrong with it rather than the last rule to fail.

    The bar is deliberately set at "could this be delivered to, and would the
    player recognise it as theirs" — not at full standards compliance. An
    address is an identity here. Refusing a real one with a queue waiting is a
    far worse failure than accepting an odd one, so every rule below rejects
    only things that cannot be an address rather than things that look unusual.
    """
    address = value.strip()
    if not address:
        return "your email — it is what remembers your best time"
    if any(character.isspace() for character in address):
        return "an address cannot have spaces in it"
    if len(address) > EMAIL_MAX:
        return "that is far too long to be an address"
    if address.count("@") != 1:
        return "an address needs exactly one @"

    local, _, domain = address.partition("@")
    return _local_error(local) or _domain_error(domain)


def _local_error(local: str) -> str | None:
    """The part before the @."""
    if not local:
        return "there is nothing before the @"
    if len(local) > LOCAL_MAX:
        return "the part before the @ is too long"

    stray = next((c for c in local if not _is_allowed(c, LOCAL_PUNCTUATION)), None)
    if stray is not None:
        return f"an address cannot contain {stray!r}"
    if local.startswith(".") or local.endswith("."):
        return "the part before the @ cannot start or end with a dot"
    if ".." in local:
        return "two dots in a row before the @"
    return None


def _domain_error(domain: str) -> str | None:
    """The part after the @."""
    if not domain:
        return "there is nothing after the @"

    stray = next((c for c in domain if not _is_allowed(c, ".-")), None)
    if stray is not None:
        return f"a domain cannot contain {stray!r}"
    if "." not in domain:
        return "the part after the @ needs a domain, like gmail.com"

    labels = domain.split(".")
    if any(not label for label in labels):
        return "the domain has an empty piece — check the dots"
    if any(len(label) > LABEL_MAX for label in labels):
        return "part of that domain is too long"
    if any(label.startswith("-") or label.endswith("-") for label in labels):
        return "a domain piece cannot start or end with a dash"
    # Checked for shape, not against a list of known endings. There are over a
    # thousand real top-level domains and a list of the handful we thought of
    # turns every other one into a person being told their own address is wrong
    # with a queue behind them — which is the exact failure this module's
    # docstring says to avoid. Two or more letters is what separates an ending
    # from a typo; `isalpha` accepts non-ASCII, so an internationalised domain
    # gets in too.
    ending = labels[-1]
    if len(ending) < 2 or not ending.isalpha():
        return "the ending does not look right — try .com or .es"
    return None


class LoginForm:
    """Name and email, and which of them the player is typing into."""

    def __init__(self) -> None:
        self.fields: list[Field | Consent] = [
            Field(label="NAME", limit=NAME_LIMIT),
            Field(label="EMAIL", limit=EMAIL_LIMIT),
            Consent(label=f"TERMS  ·  {config.TERMS_URL}"),
        ]
        self.focused = 0
        self.error: str | None = None
        self.submitted = False

    # --- what the renderer and the game ask ---------------------------------

    @property
    def name(self) -> str:
        return self.fields[0].value.strip()

    @property
    def email(self) -> str:
        return self.fields[1].value.strip()

    @property
    def consent(self) -> Consent:
        return self.fields[2]

    @property
    def accepted_at(self) -> datetime | None:
        """When the terms were agreed to, for the run about to happen."""
        return self.consent.accepted_at

    @property
    def is_empty(self) -> bool:
        """Nothing filled in yet.

        The result screen uses this to tell "race again" from "someone new is
        signing in", so that ENTER can mean both without a second key. Asked
        as `filled` rather than as `value`, because the consent box always
        has something written in it and would otherwise make every untouched
        form look half-started.
        """
        return not any(box.filled for box in self.fields)

    def clear(self) -> None:
        for box in self.fields:
            if isinstance(box, Consent):
                # Consent does not carry over to the next person. It is the
                # one field here that has to be given again by whoever is
                # about to drive.
                box.checked, box.accepted_at = False, None
            else:
                box.value = ""
        self.focused = 0
        self.error = None
        self.submitted = False

    # --- keystrokes ----------------------------------------------------------

    def handle(self, event: pygame.event.Event) -> None:
        if event.type != pygame.KEYDOWN:
            return

        if event.key in (pygame.K_TAB, pygame.K_DOWN):
            self._move(1)
        elif event.key == pygame.K_UP:
            self._move(-1)
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._enter()
        elif event.key == pygame.K_BACKSPACE:
            box = self.fields[self.focused]
            if not isinstance(box, Consent):
                box.backspace()
            self.error = None
        else:
            self._type(event.unicode)

    def _move(self, step: int) -> None:
        self.focused = (self.focused + step) % len(self.fields)
        self.error = None

    def _type(self, character: str) -> None:
        if not character or not character.isprintable():
            return

        box = self.fields[self.focused]
        if isinstance(box, Consent):
            # The space bar and nothing else, so that a stray keystroke while
            # somebody is hunting for TAB cannot agree to anything.
            if character == " ":
                box.toggle()
                self.error = None
            return

        # Nobody's address contains a space, and a trailing one typed by
        # accident is invisible and would split the same person into two rows.
        if self.focused == 1 and character.isspace():
            return
        box.type(character)
        self.error = None

    def _enter(self) -> None:
        """Move on from the name, or try to sign in from the email."""
        if self.focused == 0 and name_error(self.name) is None:
            self._move(1)
            return
        self.submit()

    def submit(self) -> bool:
        """Sign in if the whole form will do. Returns whether it worked."""
        for index, check in enumerate((name_error(self.name),
                                       email_error(self.email),
                                       consent_error(self.consent.checked))):
            if check is not None:
                self.focused = index
                self.error = check
                return False

        self.error = None
        self.submitted = True
        return True
