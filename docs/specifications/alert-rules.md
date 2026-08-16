# Alert rules and notification settings

What an operator configures, and what the configuration is allowed to be. What happens to
it afterwards is [alert dispatch](alert-dispatch.md).

---

### REQ-0014 — a rule belongs to its creator and to their network, and neither is negotiable

`user_id` is the token's `sub` and `network_id` is the caller's DSO alias. Both are taken
from the token; both are ignored if present in the request body. A caller cannot file a
rule in another operator's name or against another DSO's grid.

### REQ-0015 — a caller sees only their own rules

`WHERE user_id = :sub`, ordered oldest first. Two operators of the *same* DSO do not share
rules — rules are per person, not per network, which is also why dispatch sends one nudge
per rule rather than one per network.

Ordering is by `created_at`, which has second granularity. Rules created within the same
second tie, and their relative order is whatever the database returns.

### REQ-0016 — another operator's rule is not found

Update and delete answer `404`, not `403`, and the rule is not modified. A `403` would
confirm that the id names a real rule, which is not something a stranger should be able
to establish.

A rule id that is not a UUID is `422`; a well-formed id that names nothing is `404`.

### REQ-0017 — a rule the dispatcher could not act on is refused at the edge

`risk_types` must be a non-empty list drawn from `wind` and `heat`; `threshold` must be
`ALERT` or `WARNING`. Both are required.

Every rejected value would otherwise produce a row that dispatch silently ignores rather
than an error anyone sees: an unknown hazard matches no distribution, and an unknown
threshold falls back to the strictest floor. The `422` is the only place the operator
hears about it.

### REQ-0018 — a rule is created watching, and `active` is how it is stopped

New rules are `active` unless the body says otherwise. Deactivating removes a rule from
dispatch — the `WHERE active IS TRUE` in the dispatcher is the only thing the flag does —
while leaving it listed, owned and editable.

### REQ-0019 — an update changes only the fields it names

Fields absent from a `PATCH` body are left alone rather than reset to their defaults, so
a frontend can send a single toggle rather than the whole rule.

### REQ-0020 — notification settings exist as soon as they are asked for

`GET /api/notification-settings` creates an empty row for a caller who has none and
returns it, so the frontend never has to distinguish "never configured" from "configured
empty". `PUT` creates the row too, so configuring without ever having opened the page is
not a `404`.

The cost is a row for every caller who has ever opened the settings page.

### REQ-0021 — settings are per user, and a write merges

One row per `user_id`; one operator's settings are never visible to another.

`PUT` behaves like a `PATCH`: a body naming only `webhook_url` leaves `email_recipients`
as it was. The verb says replace and the implementation merges.
