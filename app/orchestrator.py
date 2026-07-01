"""Pipeline orchestrator: discovery -> selection -> triage -> policy gate -> remediation.

A thin control plane around Devin, which is the primitive that performs both the triage
analysis and the remediation. There are two decision points:

  selection  — which discovered candidates to process at all (handles noisy/large backlogs)
  autonomy   — how much human oversight each selected task needs (the HITL policy gate)

Selection is explicit (an operator chooses, or a single labelled issue is its own selection).
The autonomy tier is computed by the policy from Devin's HITL score, not by Devin itself.
"""
from __future__ import annotations

import threading
import time

from .config import settings
from .devin_client import DevinClient
from .github_client import GitHubClient
from .models import AutonomyTier, Finding, RemediationTask, TaskStatus, TriageAssessment
from .policy import tier_from_score
from .prompts import (REMEDIATION_SCHEMA, TRIAGE_SCHEMA, build_remediation_prompt,
                      build_triage_prompt)
from .runtime import runtime
from .store import Store


class Orchestrator:
    def __init__(self, store: Store, devin: DevinClient, github: GitHubClient | None = None):
        self.store = store
        self.devin = devin
        self.github = github
        self._lock = threading.Lock()

    # ---------- discovery -> candidates ----------
    def seed(self, findings: list[Finding]) -> list[RemediationTask]:
        """Register findings as `discovered` candidates. A finding that already maps to an
        issue in the target repo is linked (no duplicate); everything else gets a tracking
        issue created on the fork."""
        tasks = []
        for f in findings:
            if f.raw.get("issue_number") and f.raw.get("repo") == settings.github_repo:
                task = self.ingest_issue(f, f.raw["issue_number"], f.raw["issue_url"])
            else:
                task = self._register(f, create_issue=True)
            # autonomous mode: skip the manual selection gate (HITL gate still governs each)
            if runtime.autonomous_mode and task.status == TaskStatus.discovered:
                task = self.start(task.task_id, "remediate")
            tasks.append(task)
        return tasks

    def ingest_issue(self, finding: Finding, issue_number: int, issue_url: str) -> RemediationTask:
        """Register a candidate from an existing GitHub issue (does not create a new issue)."""
        return self._register(finding, issue_number=issue_number, issue_url=issue_url,
                              create_issue=False)

    def _register(self, finding: Finding, issue_number: int | None = None,
                  issue_url: str | None = None, create_issue: bool = False) -> RemediationTask:
        if (existing := self.store.get(finding.id)):
            return existing
        task = RemediationTask(task_id=finding.id, finding=finding,
                               issue_number=issue_number, issue_url=issue_url)
        if create_issue and self.github:
            task.issue_number, task.issue_url = self.github.create_issue_for_finding(
                finding, settings.remediate_label)
        self.store.upsert(task)
        return task

    # ---------- selection -> triage ----------
    def run(self, task_ids: list[str], mode: str = "remediate") -> list[RemediationTask]:
        """Operator selection: start the pipeline for the chosen candidates.

        mode: "remediate" (triage + auto-route), "triage" (evaluate only), or
        "fix" (skip triage and remediate directly).
        """
        tasks = []
        for task_id in task_ids:
            task = self.store.get(task_id)
            if not task:
                continue
            if task.status in (TaskStatus.discovered, TaskStatus.queued):
                tasks.append(self.start(task_id, mode))
            elif task.status == TaskStatus.triaged and mode in ("remediate", "fix"):
                tasks.append(self.remediate(task_id))   # already evaluated; proceed to fix
        return tasks

    def start(self, task_id: str, mode: str = "remediate") -> RemediationTask:
        """Begin processing a task: 'remediate' (triage + route), 'triage' (evaluate
        only), or 'fix' (skip triage, remediate directly). Idempotent once started."""
        task = self._require(task_id)
        if task.triage_session_id or task.remediation_session_id:
            return task
        task.run_mode = mode
        task.status = TaskStatus.queued
        self.store.upsert(task)
        return self.remediate(task_id) if mode == "fix" else self.triage(task_id)

    def triage(self, task_id: str) -> RemediationTask:
        task = self._require(task_id)
        if task.triage_session_id:                       # idempotent
            return task
        if self._active_count() >= runtime.max_concurrent_sessions:
            task.status = TaskStatus.queued              # capacity-bound; poller retries
            self.store.upsert(task)
            return task
        try:
            session = self.devin.create_session(
                prompt=build_triage_prompt(task.finding, settings.github_repo, task.issue_number),
                repos=[settings.github_repo],
                tags=["takehome", "triage", task.finding.source],
                max_acu_limit=runtime.max_acu_triage,
                devin_mode=runtime.devin_mode,
                structured_output_schema=TRIAGE_SCHEMA,
                title=f"Triage: {task.finding.title}"[:120],
            )
        except Exception as exc:
            task.status = TaskStatus.failed
            task.error = f"Could not start triage session: {exc}"
            self.store.upsert(task)
            return task
        task.triage_session_id = session.get("session_id")
        task.triage_session_url = session.get("url")
        task.status = TaskStatus.triaging
        self.store.upsert(task)
        return task

    def _on_triage_done(self, task: RemediationTask, session: dict) -> None:
        out = session.get("structured_output") or {}
        try:
            assessment = TriageAssessment(**out)
        except Exception:
            task.status = TaskStatus.failed
            task.error = "Triage produced no valid assessment"
            self.store.upsert(task)
            return
        # The policy, not the agent, sets the tier; Devin's pick is advisory.
        assessment.recommended_tier = tier_from_score(assessment.hitl_score)
        task.triage = assessment
        task.status = TaskStatus.triaged
        task.acus_consumed += session.get("acus_consumed", 0) or 0
        self.store.upsert(task)
        if self.github and task.issue_number:
            self.github.comment_on_issue(task.issue_number, self._triage_comment(assessment))
        if task.run_mode == "remediate":
            self._route(task)

    def _route(self, task: RemediationTask) -> None:
        tier = task.triage.recommended_tier
        if tier in (AutonomyTier.auto_merge, AutonomyTier.auto_pr):
            self.remediate(task.task_id)
        elif tier == AutonomyTier.approve_first:
            task.status = TaskStatus.awaiting_approval
            self.store.upsert(task)
        else:
            task.status = TaskStatus.escalated
            self.store.upsert(task)
            if self.github and task.issue_number:
                self.github.comment_on_issue(
                    task.issue_number,
                    f"Escalated to a human (HITL {task.triage.hitl_score}/100). "
                    f"Not safe to auto-remediate.\n\n{task.triage.rationale}")

    # ---------- autonomy gate ----------
    def approve(self, task_id: str) -> RemediationTask:
        task = self._require(task_id)
        if task.status == TaskStatus.awaiting_approval:
            self.remediate(task_id)
        return self._require(task_id)

    def reset(self) -> None:
        """Clear all pipeline tasks (does not touch GitHub issues or config)."""
        with self._lock:
            self.store.clear()

    def retry(self, task_id: str) -> RemediationTask:
        """Re-run a failed task in a fresh Devin session. If triage already produced a valid
        assessment, only the fix is retried; otherwise it restarts from triage."""
        task = self._require(task_id)
        if task.status != TaskStatus.failed:
            return task
        task.error = ""
        # A prior remediation session may have opened a PR we failed to capture. GitHub is the
        # source of truth, so reconcile before spending a new session (avoids a duplicate PR).
        if task.remediation_session_id:
            pr = None
            try:
                pr = self.devin.extract_pr_url(self.devin.get_session(task.remediation_session_id))
            except Exception:
                pr = None
            pr = pr or self._find_pr_on_github(task)
            if pr:
                task.pr_url = pr
                task.status = TaskStatus.succeeded
                self.store.upsert(task)
                self._maybe_automerge(task)
                return task
            task.remediation_session_id = None   # genuinely no PR; allow a fresh attempt
            task.remediation_session_url = None
        if task.triage:                          # assessment was fine; only the fix failed
            task.status = TaskStatus.triaged
            self.store.upsert(task)
            return self.remediate(task_id)
        task.triage_session_id = None            # triage itself failed; start over
        self.store.upsert(task)
        return self.start(task_id, task.run_mode)

    # ---------- remediation ----------
    def remediate(self, task_id: str) -> RemediationTask:
        task = self._require(task_id)
        if task.remediation_session_id:                  # idempotent
            return task
        if self._active_count() >= runtime.max_concurrent_sessions:
            return task                                  # capacity-bound; poller retries
        try:
            session = self.devin.create_session(
                prompt=build_remediation_prompt(task.finding, settings.github_repo,
                                                task.issue_number, self._triage_context(task)),
                repos=[settings.github_repo],
                tags=["takehome", "remediation", task.finding.source],
                max_acu_limit=runtime.max_acu_per_session,
                devin_mode=runtime.devin_mode,
                structured_output_schema=REMEDIATION_SCHEMA,
                title=f"Fix: {task.finding.title}"[:120],
            )
        except Exception as exc:
            task.status = TaskStatus.failed
            task.error = f"Could not start remediation session: {exc}"
            self.store.upsert(task)
            return task
        task.remediation_session_id = session.get("session_id")
        task.remediation_session_url = session.get("url")
        task.status = TaskStatus.remediating
        self.store.upsert(task)
        return task

    def _find_pr_on_github(self, task: RemediationTask) -> str | None:
        """GitHub is the source of truth for whether a PR exists; a session can finish
        without reporting the URL back, so we confirm against the repo."""
        if not self.github:
            return None
        return self.github.find_open_pr(task.issue_number)

    def _on_remediation_done(self, task: RemediationTask, session: dict) -> None:
        out = session.get("structured_output") or {}
        task.pr_url = (self.devin.extract_pr_url(session) or out.get("pr_url")
                       or self._find_pr_on_github(task))
        task.review_status = out.get("self_review_status")
        task.summary = out.get("summary", task.summary)
        task.acus_consumed += session.get("acus_consumed", 0) or 0
        if task.pr_url or out.get("success"):
            task.status = TaskStatus.succeeded
        else:
            task.status = TaskStatus.failed
            task.error = task.error or "Session ended without opening a PR"
        self.store.upsert(task)
        if task.status == TaskStatus.succeeded:
            self._maybe_automerge(task)

    def _maybe_automerge(self, task: RemediationTask) -> None:
        """Auto-merge the lowest-risk tier once the PR is mergeable, if enabled."""
        if (task.tier == AutonomyTier.auto_merge.value and runtime.enable_auto_merge
                and self.github and task.pr_url):
            if self.github.try_merge(task.pr_url):
                task.status = TaskStatus.merged
                self.store.upsert(task)

    # ---------- polling loop ----------
    def refresh_once(self) -> None:
        for task in self.store.all():
            if task.status == TaskStatus.queued:
                if task.run_mode == "fix":
                    self.remediate(task.task_id)
                else:
                    self.triage(task.task_id)
            elif task.status == TaskStatus.triaging and task.triage_session_id:
                s = self.devin.get_session(task.triage_session_id)
                out = s.get("structured_output") or {}
                if out.get("hitl_score") is not None:          # the assessment is ready
                    self._on_triage_done(task, s)
                elif self.devin.is_terminal(s.get("status")):
                    task.status = TaskStatus.failed
                    task.error = f"Triage ended ({s.get('status')}) without an assessment"
                    self.store.upsert(task)
            elif (task.status in (TaskStatus.remediating, TaskStatus.in_review)
                  and task.remediation_session_id):
                s = self.devin.get_session(task.remediation_session_id)
                out = s.get("structured_output") or {}
                # Finalise only on a definitive signal: an explicit success, or the session
                # genuinely ending. Devin emits success=false *while still working*, so we must
                # not treat a mid-run false as terminal.
                if out.get("success") is True or self.devin.is_terminal(s.get("status")):
                    self._on_remediation_done(task, s)
                else:
                    pr = self.devin.extract_pr_url(s)
                    if pr and task.status != TaskStatus.in_review:
                        task.status = TaskStatus.in_review
                        task.pr_url = pr
                        self.store.upsert(task)
            elif task.status == TaskStatus.triaged and not task.remediation_session_id:
                # Auto tiers proceed on their own. An approve_first task only lands here after a
                # human approved it and a retry re-queued the fix (e.g. capacity was full), so
                # resuming is safe; a fresh approve_first is already at awaiting_approval.
                if task.run_mode == "remediate" and task.tier != AutonomyTier.human_only.value:
                    self.remediate(task.task_id)
            elif (task.status == TaskStatus.succeeded and task.pr_url
                  and task.tier == AutonomyTier.auto_merge.value and runtime.enable_auto_merge):
                self._maybe_automerge(task)
            elif task.status == TaskStatus.succeeded and task.pr_url and self.github:
                if self.github.get_pr_state(task.pr_url) == "merged":   # human-merged the PR
                    task.status = TaskStatus.merged
                    self.store.upsert(task)

    def start_background_poller(self) -> None:
        def loop():
            while True:
                try:
                    with self._lock:
                        self.refresh_once()
                except Exception as exc:
                    print(f"[poller] error: {exc}")
                time.sleep(runtime.poll_interval_seconds)
        threading.Thread(target=loop, daemon=True).start()

    # ---------- helpers ----------
    def _require(self, task_id: str) -> RemediationTask:
        task = self.store.get(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _active_count(self) -> int:
        return sum(1 for t in self.store.all()
                   if t.status in (TaskStatus.triaging, TaskStatus.remediating,
                                   TaskStatus.in_review))

    @staticmethod
    def _triage_context(task: RemediationTask) -> str:
        """Carry the triage diagnosis into remediation so it doesn't re-discover the issue."""
        if not task.triage:
            return ""
        a = task.triage
        parts = [a.rationale] if a.rationale else []
        if a.risks:
            parts.append("Risks to avoid: " + "; ".join(a.risks))
        return "\n".join(parts)

    @staticmethod
    def _triage_comment(a: TriageAssessment) -> str:
        axes = (f"blast_radius `{a.blast_radius}` · reversibility `{a.reversibility}` · "
                f"verifiability `{a.verifiability}` · ambiguity `{a.ambiguity}` · "
                f"test_coverage `{a.test_coverage}`")
        risks = "\n".join(f"- {r}" for r in a.risks) or "- none noted"
        return (f"### Devin triage — HITL Requirement Score **{a.hitl_score}/100** "
                f"→ tier `{a.recommended_tier.value}`\n\n{axes}\n\n"
                f"**Rationale:** {a.rationale}\n\n**Risks:**\n{risks}")
