"""Which language `/api/me` reports.

The value feeds one consumer: `celine-frontend/apps/grid`, whose chain is
`localStorage` → this value → `getLocaleFromNavigator()` → `en`, matched against a
two-entry supported list. Everything here exists to give that chain something usable at
its second link rather than `null`.
"""

from __future__ import annotations

import pytest

from celine.grid.api.user import _from_accept_language, _primary_subtag, resolve_locale
from tests.fakes import make_service, make_user

NETWORK = "example-dso"


class Req:
    """Just the header bag — `resolve_locale` reads nothing else off the request."""

    def __init__(self, accept_language: str | None = None) -> None:
        self.headers = {}
        if accept_language is not None:
            self.headers["accept-language"] = accept_language


# ---------------------------------------------------------------------------
# Reducing a tag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("it", "it"),
        ("it-IT", "it"),
        ("IT", "it"),
        ("  en-GB  ", "en"),
        ("pt-BR", "pt"),
        ("", None),
        ("-", None),
        ("123", None),
    ],
    ids=["plain", "region", "upper", "padded", "pt-br", "empty", "dash", "digits"],
)
# @verifies REQ-0044
def test_a_tag_is_reduced_to_its_primary_subtag(tag, expected):
    """
    `pt-BR` becoming `pt` is a real loss and a deliberate one: the frontend matches the
    value against `['en', 'it']`, so anything region-qualified resolves to English
    there. A consumer that needs the region will need this to change.
    """
    assert _primary_subtag(tag) == expected


# ---------------------------------------------------------------------------
# Reading the header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("it", "it"),
        ("it-IT", "it"),
        ("it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7", "it"),
        ("en;q=0.8,it;q=0.9", "it"),
        ("en;q=0.8,it", "it"),
        ("*", None),
        ("*,it;q=0.5", "it"),
        ("en;q=0", None),
        ("en;q=nonsense", "en"),
        ("", None),
        (None, None),
        ("  ", None),
        (",,,", None),
    ],
    ids=[
        "plain", "region", "browser-default", "q-order", "implicit-q-1", "wildcard",
        "wildcard-then-real", "q-zero", "malformed-q", "empty", "absent", "blank",
        "separators",
    ],
)
# @verifies REQ-0044
def test_the_header_is_ranked_by_q_and_reduced(header, expected):
    """
    `en;q=0.8,it` is the case that catches a naive first-entry read: an unqualified tag
    defaults to `q=1` and therefore outranks the one written before it.

    `q=0` means "explicitly not this", so it is dropped rather than ranked last.
    """
    assert _from_accept_language(header) == expected


# @verifies REQ-0044
def test_ties_keep_the_order_the_browser_sent():
    """
    Equal `q` is not a preference the caller expressed twice — the order is. Sorting on
    quality alone would make the answer depend on Python's sort stability rather than on
    the header.
    """
    assert _from_accept_language("de;q=0.9,it;q=0.9,en;q=0.9") == "de"


# @verifies REQ-0044
def test_a_malformed_entry_does_not_lose_the_ones_after_it():
    """
    The header is caller-supplied and this value only picks a language, so nothing here
    may raise.
    """
    assert _from_accept_language(";;;,it") == "it"


# ---------------------------------------------------------------------------
# The two sources together
# ---------------------------------------------------------------------------


# @verifies REQ-0044
def test_the_token_claim_wins_over_the_header():
    """
    `locale` is part of the standard OIDC `profile` scope. When the realm maps it, it is
    a setting the user chose in their account rather than a browser default, so it
    outranks what the request happens to say.
    """
    user = make_user(sub="alice", orgs={NETWORK: "dso"}, locale="it")

    assert resolve_locale(user, Req("en-GB,en;q=0.9")) == "it"


# @verifies REQ-0044
def test_the_header_is_used_when_the_realm_maps_no_locale():
    """
    The case this fallback was added for. Before it, a realm that does not map the
    `profile` scope's `locale` claim left the frontend at `null` on every load.
    """
    user = make_user(sub="alice", orgs={NETWORK: "dso"})

    assert resolve_locale(user, Req("it-IT,it;q=0.9,en;q=0.8")) == "it"


@pytest.mark.parametrize("claim", ["", "   ", None, 42, [], {"it": True}])
# @verifies REQ-0044
def test_a_claim_that_is_not_a_usable_tag_falls_through_to_the_header(claim):
    """
    Including the non-string cases. Claims cross a trust boundary and this one is read
    straight out of the JWT payload, so a realm mapping `locale` to a list — which
    Keycloak does for multi-valued attributes — must fall through rather than reach the
    response as `[]`.
    """
    user = make_user(sub="alice", orgs={NETWORK: "dso"}, locale=claim)

    assert resolve_locale(user, Req("it")) == "it"


# @verifies REQ-0044
def test_neither_source_is_none_not_a_guess():
    """
    No default language is invented here. Picking `en` would be indistinguishable, to
    the frontend, from a user who asked for English — and the frontend has its own
    fallback chain, which is the right place for a default.
    """
    user = make_user(sub="alice", orgs={NETWORK: "dso"})

    assert resolve_locale(user, Req()) is None


# @verifies REQ-0044
def test_a_service_account_has_no_profile_and_falls_through():
    """
    A client-credentials token has no `profile` scope and no human behind it. It reaches
    `/api/me` only via a service integration, and the header is all there is.
    """
    assert resolve_locale(make_service(), Req("it")) == "it"
    assert resolve_locale(make_service(), Req()) is None


# ---------------------------------------------------------------------------
# Through the endpoint
# ---------------------------------------------------------------------------


# @verifies REQ-0044
async def test_the_endpoint_reports_the_header_language(client, jwt):
    headers = jwt.headers(make_user(sub="alice", orgs={NETWORK: "dso"}))
    headers["accept-language"] = "it-IT,it;q=0.9,en;q=0.8"

    response = await client.get("/api/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["user"]["locale"] == "it"


# @verifies REQ-0044
async def test_the_endpoint_prefers_the_claim(client, jwt):
    headers = jwt.headers(make_user(sub="alice", orgs={NETWORK: "dso"}, locale="en"))
    headers["accept-language"] = "it"

    response = await client.get("/api/me", headers=headers)

    assert response.json()["user"]["locale"] == "en"
