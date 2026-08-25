"""Tests for the sign-in form.

Keystrokes and validation, with no window involved. The validation rules are
the interesting part: too strict and real addresses get refused with a queue
waiting, too loose and the file fills with entries nobody can be matched to.
"""

import pygame
import pytest

from wheel_racer.login import (
    EMAIL_LIMIT,
    EMAIL_MAX,
    NAME_LIMIT,
    LoginForm,
    email_error,
    name_error,
)

NAME, EMAIL = 0, 1


def key(code: int = 0, character: str = "") -> pygame.event.Event:
    return pygame.event.Event(pygame.KEYDOWN, key=code, unicode=character)


def typed(form: LoginForm, text: str) -> None:
    for character in text:
        form.handle(key(character=character))


@pytest.fixture
def form() -> LoginForm:
    return LoginForm()


class TestTyping:
    def test_characters_land_in_the_focused_field(self, form):
        typed(form, "Lauren")
        assert form.name == "Lauren"

    def test_tab_moves_to_the_next_field(self, form):
        typed(form, "Lauren")
        form.handle(key(pygame.K_TAB))
        typed(form, "lauren@example.com")
        assert (form.name, form.email) == ("Lauren", "lauren@example.com")

    def test_tab_wraps_round(self, form):
        form.handle(key(pygame.K_TAB))
        form.handle(key(pygame.K_TAB))
        assert form.focused == NAME

    def test_backspace_deletes(self, form):
        typed(form, "Laurenn")
        form.handle(key(pygame.K_BACKSPACE))
        assert form.name == "Lauren"

    def test_backspace_on_an_empty_field_is_harmless(self, form):
        form.handle(key(pygame.K_BACKSPACE))
        assert form.name == ""

    def test_a_name_may_contain_spaces(self, form):
        """Plenty of people have two of them."""
        typed(form, "Ada Lovelace")
        assert form.name == "Ada Lovelace"

    def test_an_address_may_not(self, form):
        """A trailing space is invisible and would split one person into two."""
        form.focused = EMAIL
        typed(form, "lauren @example.com")
        assert form.email == "lauren@example.com"

    def test_control_characters_are_ignored(self, form):
        form.handle(key(character="\x00"))
        form.handle(key(character="\r"))
        assert form.name == ""

    def test_fields_have_a_length_limit(self, form):
        typed(form, "L" * (NAME_LIMIT + 30))
        assert len(form.name) == NAME_LIMIT

    def test_the_address_limit_is_longer_than_the_name(self, form):
        """Addresses are longer than names, and truncating one silently would
        lose the person it belongs to."""
        assert EMAIL_LIMIT > NAME_LIMIT

    def test_a_non_keydown_event_does_nothing(self, form):
        form.handle(pygame.event.Event(pygame.KEYUP, key=pygame.K_a, unicode="a"))
        assert form.name == ""


class TestSubmitting:
    def test_enter_moves_on_from_a_filled_name(self, form):
        typed(form, "Lauren")
        form.handle(key(pygame.K_RETURN))
        assert form.focused == EMAIL
        assert not form.submitted

    def test_enter_signs_in_from_a_filled_address(self, form):
        typed(form, "Lauren")
        form.handle(key(pygame.K_TAB))
        typed(form, "lauren@example.com")
        form.handle(key(pygame.K_RETURN))
        assert form.submitted

    def test_a_bad_address_refuses_and_says_why(self, form):
        typed(form, "Lauren")
        form.handle(key(pygame.K_TAB))
        typed(form, "lauren-at-example")
        form.handle(key(pygame.K_RETURN))
        assert not form.submitted
        assert form.error

    def test_refusing_puts_the_cursor_on_the_offending_field(self, form):
        form.focused = EMAIL
        typed(form, "lauren@example.com")
        form.submit()
        assert form.focused == NAME  # the empty one

    def test_typing_clears_a_stale_error(self, form):
        form.submit()
        assert form.error
        typed(form, "L")
        assert form.error is None

    def test_values_are_trimmed(self, form):
        typed(form, "  Lauren  ")
        assert form.name == "Lauren"


class TestEmptiness:
    def test_a_fresh_form_is_empty(self, form):
        assert form.is_empty

    def test_one_character_is_not_empty(self, form):
        """The result screen leans on this to tell "race again" from "someone
        new is signing in", so a single keystroke has to flip it."""
        typed(form, "L")
        assert not form.is_empty

    def test_clearing_empties_it(self, form):
        typed(form, "Lauren")
        form.handle(key(pygame.K_TAB))
        typed(form, "lauren@example.com")
        form.clear()
        assert form.is_empty
        assert form.focused == NAME
        assert not form.submitted


class TestValidation:
    """The bar is "could this be delivered to, and would the player recognise
    it as theirs" — not full standards compliance. Refusing a real address with
    a queue waiting is a far worse failure than accepting an odd one."""

    @pytest.mark.parametrize("address", [
        "lauren@example.com",
        "lauren.gallego-ropero@alumnos.uc3m.es",   # the club's own domain shape
        "a+tag@sub.domain.co.uk",                  # plus-tags and subdomains
        "123@example.org",
        "o'brien@example.ie",                      # apostrophes are legal
        "maría@correo.es",                         # and so are accents, in Spain
        "x@y.io",                                  # short but perfectly valid
    ])
    def test_real_addresses_are_accepted(self, address):
        assert email_error(address) is None

    @pytest.mark.parametrize("address,because", [
        ("", "left blank"),
        ("lauren", "no @ at all"),
        ("a@@b.com", "two @"),
        ("@example.com", "nothing before the @"),
        ("lauren@", "nothing after the @"),
        ("a@b", "no dot in the domain"),
        ("a@b.", "trailing dot"),
        ("a@b..com", "two dots in the domain"),
        (".a@b.com", "leading dot before the @"),
        ("a.@b.com", "trailing dot before the @"),
        ("a..b@c.com", "two dots before the @"),
        ("a@-b.com", "domain piece starts with a dash"),
        ("a@b-.com", "domain piece ends with a dash"),
        ('a"b@c.com', "a quote in it"),
        ("a,b@c.com", "a comma in it"),
        ("a b@c.com", "a space in it"),
        ("a@b.c0m", "a digit in the ending"),
        ("a@b.c", "a one-letter ending"),
    ])
    def test_typos_are_refused(self, address, because):
        assert email_error(address) is not None, because

    def test_the_message_says_what_is_wrong(self):
        """A generic "invalid email" leaves someone staring at their own
        address unable to see the problem."""
        for address, expected in [("lauren.example.com", "@"),
                                  ("@example.com", "before"),
                                  ("lauren@", "after")]:
            message = email_error(address)
            assert message is not None and expected in message

    def test_the_first_problem_is_the_one_reported(self):
        """Checked in the order a person reads an address, so the message names
        what they will notice first rather than the last rule to fail."""
        message = email_error("a b@@c")
        assert message is not None and "spaces" in message

    def test_absurd_lengths_are_refused(self):
        assert email_error("a" * EMAIL_MAX + "@example.com") is not None

    def test_a_blank_name_is_refused(self):
        assert name_error("   ") is not None

    def test_any_real_name_is_accepted(self):
        assert name_error("Ada") is None
