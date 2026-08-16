# Identity

This service authenticates nobody. It verifies a token Keycloak issued, and reads out of
it the one fact that decides everything else: which distribution network the caller
operates.

---

### REQ-0001 — every path but the four public ones requires a token

A request carrying no recognisable token is answered `401` by `PolicyMiddleware`, before
routing. `/health`, `/api/docs`, `/api/redoc` and `/api/openapi.json` are exempt.

The refusal happens before routing so that an unauthenticated caller cannot tell a real
path from an invented one; `/api/no-such-thing` is a `401`, not a `404`.

The middleware checks only that a token is *present*. Deciding whether it is valid is
`src/celine/grid/api/deps.py`'s job, and the split is deliberate: this layer hides the route table.

### REQ-0002 — a token is looked for in the proxy header, then a bearer header

`x-auth-request-access-token` — the header OAuth2 Proxy attaches — is read first, and
wins over an `Authorization` header when both are present. Failing that, an
`Authorization: Bearer` header is used, with the scheme matched case-insensitively. A
non-`Bearer` `Authorization` header is ignored rather than rejected.

The proxy header's name is configurable through `JWT_HEADER_NAME`, and **both places that
read a token honour it** — `PolicyMiddleware._has_token` and `api/deps.py::_extract_token`.
They must stay in step: the middleware runs before routing, so a gate that knew only the
default name would refuse every request the moment the setting changed, before the
dependency that honours it ever ran (#22).

The setting names *the* header, not an additional one: once changed, the default header is
no longer an identity.

### REQ-0003 — a token that does not decode is a 401

Expired, malformed, wrongly signed, or undecidable because Keycloak's JWKS endpoint could
not be reached: all four are `401`, and the detail distinguishes the first two.

The third case means a caller cannot tell "your token is forged" from "our identity
provider is down". That is a deliberate simplification and worth knowing before a wave of
`401`s is diagnosed as an attack.

### REQ-0004 — the DSO organisation's alias is the network identity

The caller's first Keycloak organisation of `type=dso` supplies its alias, and that alias
**is** the `network_id` used to query the Digital Twin, to stamp new alert rules, and to
authorise every grid request. There is no mapping table.

The type is read from either place KC 26's organisation mapper puts it: the top level of
the organisation claim, or its nested `attributes` map. An organisation of any other type
is not a network.

Renaming an organisation in Keycloak therefore changes which network this service asks
about, with no change in this repository and nothing here to grep for.

### REQ-0005 — a caller with no DSO organisation is refused, not admitted empty

`403`, with `DSO organisation membership required`. The caller is authenticated; they are
simply not a grid operator, and there is no network for them to be shown.

This applies to `/api/me` and to every grid route. It does **not** apply to reading alert
rules, which answers `200` with an empty list — see REQ-0010.
