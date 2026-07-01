"""Devin prompts for the two phases of the pipeline.

Devin is used as the primitive in both phases:
  1. TRIAGE     — an intake analysis that scores how much human oversight a change needs
  2. REMEDIATION — a focused fix that opens a PR with tests and a self-review

The HITL Requirement Score (0-100, higher = more oversight needed) is produced here and
consumed by the autonomy policy. Each phase returns a structured-output contract so the
orchestrator works from machine-readable signals rather than free text.
"""
from __future__ import annotations

from .models import Finding

# ---------- Phase 1: triage ----------
TRIAGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "hitl_score": {"type": "integer", "minimum": 0, "maximum": 100,
                       "description": "how much human oversight this change requires (higher = more)"},
        "recommended_tier": {"type": "string",
                             "enum": ["auto_merge", "auto_pr", "approve_first", "human_only"]},
        "blast_radius": {"type": "string", "enum": ["low", "medium", "high"]},
        "reversibility": {"type": "string", "enum": ["low", "medium", "high"]},
        "verifiability": {"type": "string", "enum": ["low", "medium", "high"]},
        "ambiguity": {"type": "string", "enum": ["low", "medium", "high"]},
        "test_coverage": {"type": "string", "enum": ["low", "medium", "high"]},
        "rationale": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hitl_score", "recommended_tier", "rationale", "risks"],
}


def build_triage_prompt(finding: Finding, repo: str, issue_number: int | None) -> str:
    issue_ref = f" (GitHub issue #{issue_number})" if issue_number else ""
    return f"""You are a staff engineer performing intake triage on a reported issue in `{repo}`{issue_ref}.
Do not modify any code or open a PR. Read the codebase to assess, then report.

## Issue
- Source: {finding.source} | Severity: {finding.severity.value}
- Title: {finding.title}
- Location: {finding.location or "see description"}
- Details: {finding.description}

## Produce a HITL Requirement Score (0-100)
Score how much human oversight this change requires — not how hard it is. Assess each axis
(low/medium/high) by inspecting the code:
- blast_radius: how much could break; shared/core code and many call-sites raise it.
- reversibility: easy rollback lowers it; migrations, data or config changes raise it.
- verifiability: clear acceptance criteria and tests that would catch a regression lower it.
- ambiguity: under-specified intent raises it.
- test_coverage: well-covered touched code lowers it; untested code raises it.

Combine them into hitl_score (higher = more oversight) and pick recommended_tier:
- auto_merge    (hitl < 20): trivial, fully reversible, well-tested.
- auto_pr       (20-69):     safe to fix autonomously; a human reviews the PR.
- approve_first (70-89):     a human should approve before any code changes.
- human_only    (>= 90):     needs human design or is too ambiguous to auto-remediate.

List concrete risks. When uncertain, score higher.

## Output contract
Populate the structured output with hitl_score, recommended_tier, the five axes, rationale,
and risks. Then stop without implementing anything.
"""


# ---------- Phase 2: remediation ----------
REMEDIATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean", "description": "true only if a PR was opened"},
        "pr_url": {"type": "string"},
        "summary": {"type": "string"},
        "tests_passed": {"type": "boolean"},
        "self_review_status": {"type": "string", "enum": ["clean", "changes_requested", "n/a"]},
    },
    "required": ["success", "summary"],
}


def build_remediation_prompt(finding: Finding, repo: str, issue_number: int | None,
                             triage_context: str = "") -> str:
    issue_ref = f"Reference issue #{issue_number} in the PR. " if issue_number else ""
    context = (f"\n## Triage findings (from the same codebase analysis — build on these, "
               f"don't re-discover)\n{triage_context}\n" if triage_context else "")
    return f"""You are a senior engineer making a single, focused fix in `{repo}`.
{context}
## Issue
- {finding.title} ({finding.severity.value}, {finding.source})
- Location: {finding.location or "see description"}
- Details: {finding.description}
- Suggested fix: {finding.recommendation or "use your judgement"}

## Steps
1. Branch `devin/fix-{finding.id}` (sanitise to valid git characters).
2. Make the minimal change that resolves the issue, consistent with existing patterns.
3. Add or extend tests that would catch this regression.
4. Run the relevant tests and linters; do not break the build.
5. Open a PR. {issue_ref}Describe root cause, fix, files changed, and validation.
6. Review your own PR: post a review on it — approve if it is correct and ready, or request
   changes otherwise. Make sure the checks pass so the PR is ready to merge. Record the
   outcome in self_review_status.

## Acceptance criteria (no PR unless all are met)
- The issue is genuinely resolved (not suppressed), behaviour is preserved, tests pass.

## Output contract
When the PR is open, set: {{"success": true, "pr_url": "<url>", "summary": "<what changed>",
"tests_passed": <bool>, "self_review_status": "clean" | "changes_requested" | "n/a"}}.
If you cannot safely fix it, set success=false, explain in summary, and stop.

## Guardrails
- Stay scoped to this issue. Never delete tests, disable checks, or touch CI secrets to pass.
- If the change proves riskier or more ambiguous than expected, stop and report rather than guess.
"""
