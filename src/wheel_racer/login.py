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

import pygame

NAME_LIMIT = 22
EMAIL_LIMIT = 42


@dataclass
class Field:
    """One labelled box."""

    label: str
    limit: int
    value: str = ""

    def type(self, character: str) -> None:
        if len(self.value) < self.limit:
            self.value += character

    def backspace(self) -> None:
        self.value = self.value[:-1]


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
    if labels[-1] not in ("com", "es", "edu", "org", "net"):
        return "email must end in .com, .es ..."
    ending = labels[-1]
    if len(ending) < 2 or not ending.isalpha():
        return "the ending does not look right — try .com or .es"
    return None


class LoginForm:
    """Name and email, and which of them the player is typing into."""

    def __init__(self) -> None:
        self.fields: list[Field] = [
            Field(label="NAME", limit=NAME_LIMIT),
            Field(label="EMAIL", limit=EMAIL_LIMIT),
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
    def is_empty(self) -> bool:
        """Nothing typed yet.

        The result screen uses this to tell "race again" from "someone new is
        signing in", so that ENTER can mean both without a second key.
        """
        return not any(f.value for f in self.fields)

    def clear(self) -> None:
        for box in self.fields:
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
            self.fields[self.focused].backspace()
            self.error = None
        else:
            self._type(event.unicode)

    def _move(self, step: int) -> None:
        self.focused = (self.focused + step) % len(self.fields)
        self.error = None

    def _type(self, character: str) -> None:
        if not character or not character.isprintable():
            return
        # Nobody's address contains a space, and a trailing one typed by
        # accident is invisible and would split the same person into two rows.
        if self.focused == 1 and character.isspace():
            return
        self.fields[self.focused].type(character)
        self.error = None

    def _enter(self) -> None:
        """Move on from the name, or try to sign in from the email."""
        if self.focused == 0 and name_error(self.name) is None:
            self._move(1)
            return
        self.submit()

    def submit(self) -> bool:
        """Sign in if both fields will do. Returns whether it worked."""
        for index, check in enumerate((name_error(self.name), email_error(self.email))):
            if check is not None:
                self.focused = index
                self.error = check
                return False

        self.error = None
        self.submitted = True
        return True
