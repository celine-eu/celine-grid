# Authorisation

Every decision below is made by `policies/grid.rego`, evaluated in process. **The Rego is
the specification of record.** Several docstrings in `src/celine/grid/api/deps.py` and an earlier version
of `docs/architecture.md` describe scope requirements the Rego does not impose; where they
disagree, what follows is what runs.

---

### REQ-0006 — an operator may read their own network and no other

A non-service caller passes the `read` action only when the `network_id` in the request
path equals the alias of their DSO organisation.

This is the most consequential rule in the service. A wrong `network_id` does not error at
the Digital Twin — it returns another network's valid-looking data, with no shape
difference to notice — so this comparison is the whole of the separation between two
distribution operators.

Holding a `grid.read` or `grid.admin` scope does **not** widen it. Scopes are the service
branch of the policy; a user token carrying one is still checked for ownership.

### REQ-0007 — a service account reads any network, on scope

A service account holds no organisation, so there is nothing for it to own a network
with. `grid.read` or `grid.admin` in its scopes admits it to every network's data.

### REQ-0008 — a service account without a grid scope is refused

`service missing grid.read scope`. An absent `scope` claim and a `scope` claim naming
something else land on the same denial.

### REQ-0009 — organisation membership, not the username, decides how a caller is judged

A subject carrying at least one organisation is evaluated as a **user**; only a subject
with no organisation at all can be evaluated as a **service**.

The order matters. `is_service_account()` infers from `preferred_username`, and a user
token that carries a `scope` claim but no `groups` can trip it — at which point the
caller would be judged under the service rules, where `owns_network` is never checked.
Organisation presence is checked first precisely to prevent that.

A caller who is neither — authenticated, no organisation, no service-account username —
is judged a user, which is the conservative branch.

### REQ-0010 — reading alert rules is open to every authenticated non-service caller

No scope and no organisation are required. A caller with no DSO gets `200` and an empty
list, not a `403`.

**Confidentiality of alert rules therefore rests entirely on the `WHERE user_id = :sub`
in `src/celine/grid/api/alerts.py`, not on this policy.** That is a deliberate division and it is the
reason the SQL-level isolation is tested from both sides.

### REQ-0011 — writing an alert rule requires a DSO organisation

Create, update and delete all require the caller's DSO alias to be present, because a rule
carries a `network_id` and that is the only place one comes from. Refused with
`missing DSO organization membership`.

No scope is consulted. Organisation membership is the whole check.

An operator removed from their organisation in Keycloak keeps their existing rules and
loses the ability to edit or delete them — and those rules keep firing, because dispatch
never consults the policy.

### REQ-0012 — a service account needs `grid.admin` to touch alert rules

The `alerts.*` rules are all gated on the subject not being a service, so the only rule a
service can satisfy is the blanket `grid.admin` one. `grid.read` — enough to read every
network's risk data — is not enough to read one operator's rules.

### REQ-0013 — the policy denies by default, and the engine allows by default

`default allow := false`: an action the policy does not name is refused, so a route that
grows a new action and no rule for it fails closed.

`GridAccessPolicy` inverts this in two cases, both deliberate:

- the Rego bundle did not load — `Decision(True, "no-policy-engine")`
- the evaluation raised — `Decision(True, "policy-error-permissive")`

Both are development conveniences and both are indistinguishable, from a response alone,
from a policy that ran and permitted. The reason string is the only signal, and it is
what a deployment intolerant of fail-open must alert on. The test suite refuses to run at
all unless the bundle loaded, because otherwise every assertion in this file would pass
for the wrong reason.
