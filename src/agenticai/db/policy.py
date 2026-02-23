"""Policy helpers for bypass controls and org-level policy enforcement."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenticai.db.models import BypassMode, RiskTier, RuntimeSetting, UserPolicyOverride

ORG_BYPASS_ALLOWED_KEY_TEMPLATE = "org.{org_id}.allow_user_bypass"
ORG_BYPASS_ALLOWED_GLOBAL_KEY = "org.allow_user_bypass"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def org_allows_user_bypass(session: Session, org_id: str) -> bool:
    """Return whether org policy currently allows user-level bypass overrides."""
    candidate_keys = [
        ORG_BYPASS_ALLOWED_KEY_TEMPLATE.format(org_id=org_id),
        ORG_BYPASS_ALLOWED_GLOBAL_KEY,
    ]
    for key in candidate_keys:
        setting = session.get(RuntimeSetting, key)
        if setting is None:
            continue
        parsed = _parse_bool(setting.value)
        if parsed is not None:
            return parsed
    return False


def get_user_policy_override(
    session: Session,
    *,
    org_id: str,
    user_id: str,
) -> UserPolicyOverride | None:
    """Load one user policy override row for the caller identity."""
    return session.execute(
        select(UserPolicyOverride).where(
            UserPolicyOverride.org_id == org_id,
            UserPolicyOverride.user_id == user_id,
        )
    ).scalar_one_or_none()


def resolve_effective_bypass_mode(
    session: Session,
    *,
    org_id: str,
    user_id: str,
) -> BypassMode:
    """Resolve effective bypass mode after org-policy enforcement."""
    if not org_allows_user_bypass(session, org_id):
        return BypassMode.DISABLED

    override = get_user_policy_override(session, org_id=org_id, user_id=user_id)
    if override is None:
        return BypassMode.DISABLED
    try:
        return BypassMode(override.bypass_mode)
    except ValueError:
        return BypassMode.DISABLED


def bypass_allows_risk(*, mode: BypassMode, risk_tier: RiskTier) -> bool:
    """Return True when a bypass mode permits skipping approval for a risk tier."""
    if mode == BypassMode.ALL_RISK:
        return True
    if mode == BypassMode.LOW_RISK_ONLY:
        return risk_tier in {RiskTier.LOW, RiskTier.MEDIUM}
    return False
