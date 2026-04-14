# METADATA
# title: Grid API Access Policy
# description: Controls read access to DT grid data and management of alert rules
# scope: package
# entrypoint: true
package celine.grid.access

import rego.v1

default allow := false
default reason := "access denied"

# ── helpers ───────────────────────────────────────────────────────────────────

# Service account: SubjectType.SERVICE (client-credentials grant)
is_service if {
    input.subject.type == "service"
}

has_scope(scope) if {
    scope in input.subject.scopes
}

has_any_scope(scopes) if {
    some s in scopes
    s in input.subject.scopes
}

# User's DSO org alias (stored in claims by the policy wrapper) matches network_id
owns_network if {
    input.subject.claims.network_id != null
    input.resource.attributes.network_id == input.subject.claims.network_id
}

# ── grid data (DT proxy) ──────────────────────────────────────────────────────

# DSO users: org membership is the authorisation signal — no extra scope needed.
# Keycloak has already verified the org; we only check that the requested
# network_id matches their DSO alias.
allow if {
    not is_service
    input.action.name == "read"
    owns_network
}

# Services: no org membership, so scope is required to express intent.
allow if {
    is_service
    input.action.name == "read"
    has_any_scope(["grid.read", "grid.admin"])
}

# ── alert rules ───────────────────────────────────────────────────────────────

# DSO users: any authenticated DSO member may read their own rules.
# Ownership is enforced at the DB query level (user_id == sub).
allow if {
    not is_service
    input.action.name == "alerts.read"
}

# DSO users: write requires explicit scope — alert management is opt-in.
allow if {
    not is_service
    input.action.name == "alerts.write"
    has_any_scope(["grid.alerts.write", "grid.admin"])
}

# Service full access
allow if {
    is_service
    has_scope("grid.admin")
}

# ── reasons ───────────────────────────────────────────────────────────────────

reason := "user accessing own network data" if {
    not is_service
    input.action.name == "read"
    owns_network
}

reason := "user managing own alert rules" if {
    not is_service
    input.action.name in {"alerts.read", "alerts.write"}
    allow
}

reason := "service access granted" if {
    is_service
    allow
}

reason := "network_id mismatch: user does not belong to the requested DSO" if {
    not allow
    not is_service
    input.action.name == "read"
    not owns_network
}

reason := "missing grid.alerts.write scope" if {
    not allow
    not is_service
    input.action.name == "alerts.write"
    not has_any_scope(["grid.alerts.write", "grid.admin"])
}

reason := "service missing grid.read scope" if {
    not allow
    is_service
    input.action.name == "read"
    not has_any_scope(["grid.read", "grid.admin"])
}
