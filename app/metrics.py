"""Roll task state up into the signals an engineering leader cares about.

Two halves of "how would a leader know this is working?":
  - trust: how work was routed by HITL score, how much ran autonomously vs gated vs escalated
  - throughput: PRs opened, work merged, and an estimate of engineer-hours saved
"""
from __future__ import annotations

from .models import RemediationTask, TaskStatus
from .runtime import runtime


def summarise(tasks: list[RemediationTask]) -> dict:
    total = len(tasks)
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    hitl_scores: list[int] = []
    for t in tasks:
        by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
        if t.tier:
            by_tier[t.tier] = by_tier.get(t.tier, 0) + 1
        if t.hitl_score is not None:
            hitl_scores.append(t.hitl_score)

    discovered = by_status.get(TaskStatus.discovered.value, 0)
    merged = by_status.get(TaskStatus.merged.value, 0)
    succeeded = by_status.get(TaskStatus.succeeded.value, 0) + merged
    failed = by_status.get(TaskStatus.failed.value, 0)
    escalated = by_status.get(TaskStatus.escalated.value, 0)
    finished = succeeded + failed
    prs_opened = sum(1 for t in tasks if t.pr_url)
    triaged = sum(1 for t in tasks if t.triage is not None)
    acus = sum(t.acus_consumed for t in tasks)

    hours_saved = succeeded * runtime.engineer_hours_per_fix

    return {
        "total_tasks": total,
        "candidates": discovered,
        "selected": total - discovered,
        "triaged": triaged,
        "by_status": by_status,
        "by_tier": by_tier,
        "avg_hitl_score": round(sum(hitl_scores) / len(hitl_scores), 1) if hitl_scores else None,
        "escalated_to_human": escalated,
        "prs_opened": prs_opened,
        "merged": merged,
        "success_rate": round(succeeded / finished, 3) if finished else None,
        "acus_consumed": round(acus, 2),
        "engineer_hours_saved": round(hours_saved, 1),
    }
