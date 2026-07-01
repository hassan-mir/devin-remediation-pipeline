"""Autonomy policy: map a HITL Requirement Score to an autonomy tier.

The mapping is enforced in this control plane rather than by the triage agent. The
agent reports how much human oversight a change needs; the policy decides what that
means for automation. Keeping the decision here means the model cannot escalate its
own autonomy, and the thresholds can be tuned per team without touching prompts.
"""
from __future__ import annotations

from .models import AutonomyTier
from .runtime import runtime


def tier_from_score(score: int) -> AutonomyTier:
    if score < runtime.hitl_auto_merge_below:
        return AutonomyTier.auto_merge
    if score < runtime.hitl_auto_pr_below:
        return AutonomyTier.auto_pr
    if score < runtime.hitl_approve_first_below:
        return AutonomyTier.approve_first
    return AutonomyTier.human_only
