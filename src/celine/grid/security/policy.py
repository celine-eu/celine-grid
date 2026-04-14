"""OPA access policy for the grid API.

Uses celine.sdk.policies.PolicyEngine.evaluate_decision — the correct high-level
API that builds proper ``data.{package}.allow`` / ``data.{package}.reason``
queries rather than evaluating the package path as a raw Rego expression.

Falls back to permissive when the engine is unavailable (dev/test convenience).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from celine.sdk.auth import JwtUser

logger = logging.getLogger(__name__)

_POLICIES_DIR = Path(__file__).parent.parent.parent.parent.parent / "policies"
_PACKAGE = "celine.grid.access"

DSO_TYPE = "dso"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None


def _dso_network(user: JwtUser) -> str | None:
    for org in user.organizations:
        if org.type == DSO_TYPE:
            return org.alias
    return None


def _make_policy_input(user: JwtUser, action: str, attributes: dict):
    """Build a PolicyInput for the SDK evaluate_decision API."""
    from celine.sdk.policies import PolicyInput, Subject, Resource, Action, SubjectType, ResourceType

    scopes_str = user.claims.get("scope") or ""
    scopes = scopes_str.split() if isinstance(scopes_str, str) else list(scopes_str)

    # Service accounts (client-credentials grant) never carry organisation memberships.
    # DSO operators always do. Prefer org-presence as the authoritative signal —
    # is_service_account() can misfire when a user JWT has a `scope` claim but
    # no Keycloak `groups`, causing it to be treated as a service account.
    if user.organizations:
        subject_type = SubjectType.USER
    elif user.is_service_account:
        subject_type = SubjectType.SERVICE
    else:
        subject_type = SubjectType.USER

    groups = user.claims.get("groups") or []

    return PolicyInput(
        subject=Subject(
            id=user.sub,
            type=subject_type,
            groups=groups,
            scopes=scopes,
            # Pass grid-specific extras in claims so rego can access them
            claims={
                "network_id": None if user.is_service_account else _dso_network(user),
            },
        ),
        resource=Resource(
            # ResourceType.USERDATA used as a generic stand-in — grid.rego does not
            # inspect resource.type, only resource.attributes
            type=ResourceType.USERDATA,
            id="grid",
            attributes=attributes,
        ),
        action=Action(name=action),
    )


class GridAccessPolicy:
    """Enforce OPA grid policies via celine.sdk.policies.PolicyEngine.

    Instantiated once at module import; decisions are evaluated per request.
    Falls back to permissive (allow=True) when the policy engine is unavailable
    so development environments without OPA continue to work.
    """

    def __init__(self) -> None:
        self._engine = None
        try:
            from celine.sdk.policies import PolicyEngine

            if _POLICIES_DIR.exists():
                self._engine = PolicyEngine(policies_dir=str(_POLICIES_DIR))
                self._engine.load()
                logger.info("OPA policy engine loaded from %s", _POLICIES_DIR)
            else:
                logger.warning(
                    "Policies dir %s not found — running without OPA", _POLICIES_DIR
                )
        except ImportError:
            logger.warning("celine.sdk.policies not available — running without OPA")

    async def _evaluate(self, user: JwtUser, action: str, attributes: dict) -> Decision:
        if self._engine is None:
            return Decision(True, "no-policy-engine")
        try:
            policy_input = _make_policy_input(user, action, attributes)
            result = self._engine.evaluate_decision(_PACKAGE, policy_input)
            decision = Decision(allowed=result.allowed, reason=result.reason or None)
            if not decision.allowed:
                logger.warning(
                    "Access denied sub=%s action=%s attributes=%s reason=%s",
                    user.sub, action, attributes, decision.reason,
                )
            else:
                logger.debug(
                    "Access granted sub=%s action=%s reason=%s",
                    user.sub, action, decision.reason,
                )
            return decision
        except Exception as exc:
            logger.warning("OPA evaluation error: %s", exc)
            return Decision(True, "policy-error-permissive")

    async def allow_network_read(self, user: JwtUser, network_id: str) -> Decision:
        """Check if *user* may read DT data for *network_id*.

        - Users: must hold ``grid.read`` / ``grid.admin`` **and** belong to the
          DSO organisation whose alias equals *network_id*.
        - Service accounts: must hold ``grid.read`` / ``grid.admin``; no
          ownership check.
        """
        return await self._evaluate(user, "read", {"network_id": network_id})

    async def allow_alerts_read(self, user: JwtUser) -> Decision:
        """Require ``grid.alerts.read``, ``grid.alerts.write``, or ``grid.admin``."""
        return await self._evaluate(user, "alerts.read", {})

    async def allow_alerts_write(self, user: JwtUser) -> Decision:
        """Require ``grid.alerts.write`` or ``grid.admin``."""
        return await self._evaluate(user, "alerts.write", {})


# Module-level singleton — loaded once, reused across requests
policy = GridAccessPolicy()
