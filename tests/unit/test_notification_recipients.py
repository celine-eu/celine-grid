"""Parsing a recipient list, and the identity an email list is given.

Both are pure functions, and both decide who receives an alert — the first by dropping
what it cannot parse, the second by collapsing a list of addresses to one stable id that
nudging-tool treats as a user.
"""

from __future__ import annotations

import pytest

from celine.grid.services.notification_recipients import (
    parse_recipients,
    synthetic_email_user_id,
)


# ---------------------------------------------------------------------------
# parse_recipients
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a@example.test", ["a@example.test"]),
        ("a@example.test,b@example.test", ["a@example.test", "b@example.test"]),
        ("a@example.test; b@example.test", ["a@example.test", "b@example.test"]),
        ("a@example.test b@example.test", ["a@example.test", "b@example.test"]),
        ("a@example.test,\n  b@example.test", ["a@example.test", "b@example.test"]),
    ],
    ids=["one", "comma", "semicolon", "space", "newline"],
)
# @verifies REQ-0035
def test_a_recipient_list_is_split_on_any_of_comma_semicolon_or_whitespace(
    raw, expected
):
    """
    The field is free text typed by an operator into a settings form, so all three
    separators are in real data.
    """
    assert parse_recipients(raw) == expected


@pytest.mark.parametrize(
    "raw", [None, "", "   ", ",;,", "not-an-email", "@example.test", "a@b"],
    ids=["none", "empty", "blank", "separators", "no-at", "no-local", "no-tld"],
)
# @verifies REQ-0035
def test_anything_that_is_not_an_address_is_dropped(raw):
    """
    Silently. A rule whose recipients are all unparseable falls back to the user's
    notification settings and, failing that, sends to `rule.user_id` — it never errors
    and never tells the operator their typo cost them the alert. One of seven such
    paths; see `.agents/knowledge/silence-is-the-failure-mode.md`.
    """
    assert parse_recipients(raw) == []


# @verifies REQ-0035
def test_a_valid_address_survives_alongside_an_invalid_one():
    assert parse_recipients("oops,, real@example.test") == ["real@example.test"]


# @verifies REQ-0035
def test_duplicates_are_dropped_case_insensitively_keeping_the_first_spelling():
    """
    Case is preserved in what is sent — mail servers may treat the local part as
    case-sensitive — but not in what counts as a duplicate, so an operator listing an
    address twice in two spellings is not mailed twice.
    """
    assert parse_recipients("Ops@Example.test, ops@example.test") == ["Ops@Example.test"]


# @verifies REQ-0035
def test_the_given_order_is_kept():
    assert parse_recipients("b@example.test,a@example.test") == [
        "b@example.test",
        "a@example.test",
    ]


# ---------------------------------------------------------------------------
# synthetic_email_user_id
# ---------------------------------------------------------------------------


# @verifies REQ-0036
def test_the_same_recipients_always_produce_the_same_id():
    """
    nudging-tool keys delivery preferences and de-duplication off `user_id`. A id that
    changed between runs would make every alert look like a new recipient.
    """
    first = synthetic_email_user_id(["a@example.test", "b@example.test"])
    second = synthetic_email_user_id(["a@example.test", "b@example.test"])

    assert first == second
    assert first.startswith("email-ingest:")


# @verifies REQ-0036
def test_order_and_case_do_not_change_the_id():
    """
    The digest is taken over the sorted, lower-cased list, so an operator reordering
    the recipients field does not create a second recipient in nudging-tool.
    """
    assert synthetic_email_user_id(["b@example.test", "A@example.test"]) == (
        synthetic_email_user_id(["a@example.test", "B@example.test"])
    )


# @verifies REQ-0036
def test_a_different_recipient_set_produces_a_different_id():
    assert synthetic_email_user_id(["a@example.test"]) != synthetic_email_user_id(
        ["a@example.test", "b@example.test"]
    )


# @verifies REQ-0036
def test_the_id_is_a_truncated_digest_and_not_the_addresses():
    """
    16 hex characters of SHA-256. The addresses are in the `facts.email_recipients`
    field of the same payload, so this is not a privacy measure — it is a stable key.
    """
    user_id = synthetic_email_user_id(["operator@example.test"])
    prefix, digest = user_id.split(":")

    assert prefix == "email-ingest"
    assert len(digest) == 16
    assert "operator" not in user_id
