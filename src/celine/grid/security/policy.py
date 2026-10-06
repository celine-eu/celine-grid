"""OPA access policy for the grid API.

Uses celine.sdk.policies.PolicyEngine.evaluate_decision — the correct high-level
API that builds proper ``data.{package}.allow`` / ``data.{package}.reason``
queries rather than evaluating the package path as a raw Rego expression.

**Fails closed outside development.** A missing engine or an evaluation that raises
is a denial unless ``CELINE_ENV=dev`` (``celine.sdk.posture``); a missing engine also
refuses startup there (``security/posture.py``). Only in dev do both degrade to an
allow with a warning, so a laptop without the bundle keeps working.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from celine.sdk.auth import JwtUser, organization_groups
from celine.sdk.posture import is_dev

from celine.grid.settings import settings

logger = logging.getLogger(__name__)

_PACKAGE = "celine.grid.access"

DSO_TYPE = "dso"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None


def dso_networks(user: JwtUser) -> list[str]:
    """The aliases of the caller's DSO organisations, sorted."""
    return sorted(
        org.alias
        for org in user.organizations
        if org.type == DSO_TYPE or org.has_attribute("type", DSO_TYPE)
    )


def _dso_network(user: JwtUser, requested: str | None = None) -> str | None:
    """The DSO network the request is about, from the caller's side (REQ-0052).

    The network the request names, when the caller is in it; else the caller's one
    DSO. ``None`` when the caller has no DSO, or has several and the request names
    none of them: two networks are never reduced to the first one found.
    """
    networks = dso_networks(user)
    if requested is not None and requested in networks:
        return requested
    if len(networks) == 1:
        return networks[0]
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

    network_id = (
        None
        if subject_type == SubjectType.SERVICE
        else _dso_network(user, attributes.get("network_id"))
    )

    # REQ-0051: groups count only inside the organisation the request concerns — the
    # network it names, else the caller's own DSO. Never realm groups, never another
    # organisation's, never a merge of the two levels. Realm roles (`platform-admin`
    # included) are deliberately absent: this service has no platform-wide grant.
    concerned = attributes.get("network_id") or network_id
    groups = (
        organization_groups(user.claims, concerned)
        if concerned and subject_type != SubjectType.SERVICE
        else []
    )

    return PolicyInput(
        subject=Subject(
            id=user.sub,
            type=subject_type,
            groups=groups,
            scopes=scopes,
            # Pass grid-specific extras in claims so rego can access them
            claims={"network_id": network_id, "dso_count": len(dso_networks(user))},
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

    When the engine is unavailable or an evaluation raises, the decision is a denial
    unless ``CELINE_ENV=dev``, where it is an allow with a warning so development
    without OPA keeps working. The posture is read per decision, not at import.
    """

    def __init__(self) -> None:
        self._engine = None
        try:
            from celine.sdk.policies import PolicyEngine

            policies_dir = settings.policies.policies_dir
            if policies_dir.exists():
                self._engine = PolicyEngine(policies_dir=str(policies_dir))
                self._engine.load()
                logger.info("OPA policy engine loaded from %s", policies_dir)
            else:
                logger.warning(
                    "Policies dir %s not found — running without OPA", policies_dir
                )
        except ImportError:
            logger.warning("celine.sdk.policies not available — running without OPA")

    @property
    def loaded(self) -> bool:
        """True when a Rego bundle is loaded and decisions are real."""
        return self._engine is not None

    async def _evaluate(self, user: JwtUser, action: str, attributes: dict) -> Decision:
        if self._engine is None:
            if is_dev():
                logger.warning("No policy engine — allowing %r (CELINE_ENV=dev)", action)
                return Decision(True, "no-policy-engine")
            logger.error("No policy engine — denying %r (fail closed)", action)
            return Decision(False, "no-policy-engine")
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
            if is_dev():
                logger.warning("OPA evaluation error, allowing (CELINE_ENV=dev): %s", exc)
                return Decision(True, "policy-error-permissive")
            logger.error("OPA evaluation error, denying (fail closed): %s", exc)
            return Decision(False, "policy-error")

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
