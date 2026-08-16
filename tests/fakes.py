"""Doubles for everything this repository does not own.

Each one is written from what *this* code reaches for, not from the SDK's own classes.
That is a deliberate cut with a stated cost — see
`.agents/knowledge/faking-the-sdk-boundary.md` — and it is the reason a green run says
nothing about whether `celine-sdk` still returns these shapes.
"""

from __future__ import annotations

from typing import Any

from celine.sdk.auth.jwt import JwtUser, Organization

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def make_user(
    *,
    sub: str,
    orgs: dict[str, str | None] | None = None,
    email: str | None = None,
    scope: str | None = None,
    preferred_username: str | None = None,
    org_attributes: dict[str, dict[str, list[str]]] | None = None,
    **claims: Any,
) -> JwtUser:
    """Build a `JwtUser` the way `JwtUser.from_token` would from these claims.

    `orgs` maps a Keycloak organisation alias to its `type` (``"dso"``, or `None` for an
    organisation with no type). The `organization` claim is assembled and then parsed by
    the SDK's own `Organization._from_claim`, so this fake never gets to disagree with
    the SDK about how an organisation claim is read — which matters, because the alias
    that comes out of it *is* the `network_id`.

    `org_attributes` puts values in the nested `attributes` map instead, which is the
    other shape KC 26's org mapper emits.
    """
    org_claim: dict[str, dict[str, Any]] = {}
    for alias, org_type in (orgs or {}).items():
        data: dict[str, Any] = {"id": f"id-{alias}"}
        if org_type is not None:
            data["type"] = [org_type]
        if org_attributes and alias in org_attributes:
            data["attributes"] = org_attributes[alias]
        org_claim[alias] = data

    payload: dict[str, Any] = {"sub": sub, "organization": org_claim, **claims}
    if email is not None:
        payload["email"] = email
    if scope is not None:
        payload["scope"] = scope
    if preferred_username is not None:
        payload["preferred_username"] = preferred_username

    # Populate the same dataclass fields `JwtUser.from_token` lifts out of the payload,
    # and no others. A claim this list forgets stays reachable only through `.claims` —
    # which is exactly the distinction that made `MeUser.locale` structurally dead, so a
    # fake that filled in every field from the payload would have hidden that defect.
    return JwtUser(
        sub=sub,
        email=payload.get("email"),
        email_verified=payload.get("email_verified"),
        name=payload.get("name"),
        given_name=payload.get("given_name"),
        family_name=payload.get("family_name"),
        preferred_username=payload.get("preferred_username"),
        iss=payload.get("iss"),
        aud=payload.get("aud"),
        exp=payload.get("exp"),
        iat=payload.get("iat"),
        organizations=[
            Organization._from_claim(alias, data) for alias, data in org_claim.items()
        ],
        claims=payload,
        token="fake-token",
    )


def make_service(client_id: str = "svc-grid", *, scope: str = "") -> JwtUser:
    """A client-credentials principal, as Keycloak issues one.

    `preferred_username = service-account-<client_id>` is the signal
    `is_service_account()` treats as authoritative, and no organisation membership is
    what makes `_make_policy_input` type it `SERVICE`.
    """
    return make_user(
        sub=f"service-account-{client_id}",
        orgs={},
        scope=scope,
        preferred_username=f"service-account-{client_id}",
        client_id=client_id,
    )


class FakeJwt:
    """Stands in for the `JwtUser` class as `api/deps.py` uses it.

    Only `from_token` is replaced. Everything the deps module does around it — reading
    the two headers in order, mapping the exception to a 401 — stays under test.

    `raises` makes the next decode fail with a chosen exception, which is how the
    expired- and invalid-token branches are reached without minting real JWTs.
    """

    def __init__(self) -> None:
        self._users: dict[str, JwtUser] = {}
        self.raises: BaseException | None = None
        self.decoded: list[str] = []

    # -- the part `deps.py` calls --

    def from_token(self, token: str, oidc: Any = None, **_: Any) -> JwtUser:
        self.decoded.append(token)
        if self.raises is not None:
            raise self.raises
        user = self._users.get(token)
        if user is None:
            raise ValueError(f"no fake user registered for token {token!r}")
        return user

    # -- test-facing helpers --

    def mint(self, user: JwtUser) -> str:
        token = f"token-{user.sub}-{len(self._users)}"
        self._users[token] = user
        return token

    def headers(self, user: JwtUser, *, header: str | None = None) -> dict[str, str]:
        """Headers carrying a token for *user*, in the production header by default."""
        name = header or "x-auth-request-access-token"
        return {name: self.mint(user)}

    def bearer(self, user: JwtUser) -> dict[str, str]:
        return {"authorization": f"Bearer {self.mint(user)}"}


# ---------------------------------------------------------------------------
# Digital Twin
# ---------------------------------------------------------------------------


class FetchResult:
    """Shaped like the SDK's `FetchResultSchema` as the grid routes read one.

    They call `.to_dict()` and index `["items"]`; nothing else about it is touched.
    """

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    def to_dict(self) -> dict[str, Any]:
        return {"items": [dict(item) for item in self.items], "count": len(self.items)}


class DTError(RuntimeError):
    """Constructed lazily so tests import `DTApiError` from the SDK, not from here."""


class FakeGridClient:
    """The `dt.grid` namespace.

    Every method returns whatever `set(name, value)` last put there, and records the
    call. A method set to an exception instance raises it — that is how the `DTApiError`
    mapping in `api/grid.py` is exercised.
    """

    _DEFAULTS: dict[str, Any] = {
        "wind_map": {"type": "FeatureCollection", "features": []},
        "wind_bosco": {"items": []},
        "wind_alert_distribution": [],
        "wind_trend": [],
        "heat_map": {"type": "FeatureCollection", "features": []},
        "heat_alert_distribution": [],
        "heat_trend": [],
        "substations_map": {"items": []},
        "filters": {"operational_unit": [], "line_name": []},
        "summary": {"lines": 0},
    }

    def __init__(self) -> None:
        self.responses: dict[str, Any] = dict(self._DEFAULTS)
        self.responses.update(
            {
                "tile_index": FetchResult([]),
                "shapes": FetchResult([]),
                "risks": FetchResult([]),
                "risks_now": FetchResult([]),
                "trendline": FetchResult([]),
            }
        )
        self.calls: list[tuple[str, tuple, dict]] = []

    def set(self, name: str, value: Any) -> None:
        self.responses[name] = value

    def call_kwargs(self, name: str) -> dict[str, Any]:
        """The kwargs of the last call to *name*."""
        for called, _args, kwargs in reversed(self.calls):
            if called == name:
                return kwargs
        raise AssertionError(f"{name} was never called")

    def called(self, name: str) -> int:
        return sum(1 for called, _a, _k in self.calls if called == name)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        async def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            value = self.responses.get(name)
            if isinstance(value, BaseException):
                raise value
            return value

        return _call


class FakeDTClient:
    """Stands in for `celine.sdk.dt.DTClient`. Only `.grid` is ever reached."""

    def __init__(self) -> None:
        self.grid = FakeGridClient()


# ---------------------------------------------------------------------------
# nudging-tool
# ---------------------------------------------------------------------------


class FakeNudgingClient:
    """Stands in for `NudgingAdminClient`. Only `ingest_event` is ever reached.

    Events are kept as the `DigitalTwinEvent` the dispatcher built, so a test asserting
    on a payload is asserting on something that survived
    `DigitalTwinEvent.from_dict` — the one place a malformed payload would show up.
    """

    def __init__(self, *, fails: bool = False) -> None:
        self.events: list[Any] = []
        self.fails = fails

    async def ingest_event(self, event: Any) -> None:
        if self.fails:
            raise RuntimeError("nudging unavailable")
        self.events.append(event)

    def payloads(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.events]
