"""Domain models for the remediation pipeline.

Finding          -> a candidate surfaced by a discovery channel (vuln, bug, quality, etc.)
TriageAssessment -> Devin's intake analysis: a HITL Requirement Score, risk axes, and tier
RemediationTask  -> one unit of work threaded through discovery -> selection -> triage ->
                    policy gate -> remediation
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"
    unknown = "unknown"


class Finding(BaseModel):
    id: str
    source: str
    severity: Severity
    title: str
    description: str
    location: str = ""
    recommendation: str = ""
    raw: dict = Field(default_factory=dict)


class AutonomyTier(str, Enum):
    """How much human oversight a task requires — the routing decision."""
    auto_merge = "auto_merge"        # fix + self-review + (policy-gated) auto-merge
    auto_pr = "auto_pr"              # fix -> PR -> human review
    approve_first = "approve_first"  # human approves before remediation
    human_only = "human_only"        # analysis only, no PR


class TriageAssessment(BaseModel):
    hitl_score: int                  # 0-100; higher = more human oversight required
    recommended_tier: AutonomyTier
    blast_radius: str = "medium"
    reversibility: str = "medium"
    verifiability: str = "medium"
    ambiguity: str = "medium"
    test_coverage: str = "medium"
    rationale: str = ""
    risks: list[str] = Field(default_factory=list)


class TaskStatus(str, Enum):
    """Issue lifecycle, from discovery through remediation."""
    discovered = "discovered"            # surfaced by a channel, awaiting selection
    queued = "queued"                    # selected, waiting for triage capacity
    triaging = "triaging"                # triage session running
    triaged = "triaged"                  # triage complete, tier decided
    awaiting_approval = "awaiting_approval"  # approve_first tier: waiting on a human
    remediating = "remediating"          # remediation session running
    in_review = "in_review"              # PR open; self-review / CI in progress
    succeeded = "succeeded"              # PR open and review clean
    merged = "merged"                    # PR auto-merged (auto_merge tier, when enabled)
    escalated = "escalated"              # human_only: analysis posted, no PR
    failed = "failed"


class RemediationTask(BaseModel):
    task_id: str
    finding: Finding
    issue_number: Optional[int] = None
    issue_url: Optional[str] = None
    status: TaskStatus = TaskStatus.discovered
    run_mode: str = "remediate"           # remediate | triage (evaluate only) | fix (skip triage)

    # triage phase
    triage_session_id: Optional[str] = None
    triage_session_url: Optional[str] = None
    triage: Optional[TriageAssessment] = None

    # remediation phase
    remediation_session_id: Optional[str] = None
    remediation_session_url: Optional[str] = None
    pr_url: Optional[str] = None
    review_status: Optional[str] = None   # "clean" | "changes_requested" | None
    acus_consumed: float = 0.0
    summary: str = ""
    error: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def hitl_score(self) -> Optional[int]:
        return self.triage.hitl_score if self.triage else None

    @computed_field
    @property
    def tier(self) -> Optional[str]:
        return self.triage.recommended_tier.value if self.triage else None
