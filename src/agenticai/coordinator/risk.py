"""Risk classification for task approval gating."""

from __future__ import annotations

from dataclasses import dataclass

from agenticai.db.models import RiskTier

_CRITICAL_MARKERS = {
    "rm -rf",
    "drop database",
    "truncate table",
    "format disk",
    "shutdown",
}
_HIGH_RISK_MARKERS = {
    "delete",
    "destroy",
    "revoke",
    "production",
    "sudo",
    "exfiltrate",
}


@dataclass(frozen=True)
class RiskAssessment:
    """Risk score output consumed by coordinator approval logic."""

    tier: RiskTier
    requires_approval: bool
    rationale: str | None = None


def classify_task_risk(prompt: str | None) -> RiskAssessment:
    """Classify one task prompt into a risk tier and approval requirement."""
    if prompt is None or not prompt.strip():
        return RiskAssessment(tier=RiskTier.LOW, requires_approval=False)

    normalized_prompt = prompt.strip().lower()
    for marker in _CRITICAL_MARKERS:
        if marker in normalized_prompt:
            return RiskAssessment(
                tier=RiskTier.CRITICAL,
                requires_approval=True,
                rationale=f"Matched critical marker: '{marker}'",
            )

    for marker in _HIGH_RISK_MARKERS:
        if marker in normalized_prompt:
            return RiskAssessment(
                tier=RiskTier.HIGH,
                requires_approval=True,
                rationale=f"Matched high-risk marker: '{marker}'",
            )

    if len(normalized_prompt) > 2048:
        return RiskAssessment(
            tier=RiskTier.MEDIUM,
            requires_approval=False,
            rationale="Long prompt exceeded 2048 characters",
        )
    return RiskAssessment(tier=RiskTier.LOW, requires_approval=False)
