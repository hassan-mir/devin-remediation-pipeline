"""GitHub helper: create labelled issues, comment triage reports, and merge PRs.

Devin opens PRs itself (via the GitHub App connected in the Devin org UI). This client
is only for the pipeline's own GitHub actions: seeding issues from scanner findings,
posting triage reports, and (optional) auto-merge.
"""
from __future__ import annotations

import re

from github import Github

from .models import Finding


class GitHubClient:
    def __init__(self, token: str, repo: str):
        self._gh = Github(token)
        self._repo = self._gh.get_repo(repo)

    def ensure_label(self, name: str, color: str = "5319e7") -> None:
        try:
            self._repo.get_label(name)
        except Exception:
            self._repo.create_label(name=name, color=color, description="Auto-remediate with Devin")

    def create_issue_for_finding(self, finding: Finding, label: str) -> tuple[int, str]:
        """Create a GitHub issue for a finding. Returns (issue_number, issue_url)."""
        self.ensure_label(label)
        body = (
            f"**Source:** {finding.source}\n"
            f"**Severity:** {finding.severity.value}\n"
            f"**Location:** {finding.location or 'n/a'}\n\n"
            f"{finding.description}\n\n"
            f"**Suggested remediation:** {finding.recommendation or 'see description'}\n\n"
            f"<!-- finding-id: {finding.id} -->"  # machine-traceable back-reference
        )
        issue = self._repo.create_issue(
            title=f"[{finding.severity.value}] {finding.title}",
            body=body,
            labels=[label],
        )
        return issue.number, issue.html_url

    def comment_on_issue(self, issue_number: int, body: str) -> None:
        """Post a comment (e.g. the triage report) back onto the issue, so engineers
        see the same signals in the issue and the dashboard."""
        try:
            self._repo.get_issue(issue_number).create_comment(body)
        except Exception as exc:  # don't let a comment failure break the pipeline
            print(f"[github] could not comment on #{issue_number}: {exc}")

    def try_merge(self, pr_url: str) -> bool:
        """Best-effort squash-merge if the PR is mergeable. Returns True once merged."""
        try:
            number = int(pr_url.rstrip("/").split("/")[-1])
            pr = self._repo.get_pull(number)
            if pr.merged:
                return True
            if pr.draft or pr.mergeable is False:
                return False
            if pr.mergeable and pr.mergeable_state not in ("dirty", "blocked", "behind"):
                pr.merge(merge_method="squash")
                return True
        except Exception as exc:
            print(f"[github] auto-merge skipped for {pr_url}: {exc}")
        return False

    def find_open_pr(self, issue_number: int | None, branch_prefix: str = "devin/") -> str | None:
        """Find the open PR Devin opened for this issue. GitHub is the source of truth for
        whether a PR exists: a session can finish without reporting the URL back, so we look
        it up rather than trust the agent's self-report. Matches strictly, by the issue
        reference in the PR or the issue number embedded in a `devin/` branch, so concurrent
        tasks never collide on the same PR (returns None rather than guess)."""
        if not issue_number:
            return None
        # bound both matches to the exact number, so #4 never matches #40 (or a fix-...-40 branch)
        ref_token = re.compile(rf"#{issue_number}(?!\d)")
        num_token = re.compile(rf"(?<!\d){issue_number}(?!\d)")
        try:
            scanned = 0
            for pr in self._repo.get_pulls(state="open", sort="created", direction="desc"):
                scanned += 1
                if scanned > 60:
                    break
                head = pr.head.ref or ""
                if ref_token.search(f"{pr.title or ''}\n{pr.body or ''}"):   # PR references the issue
                    return pr.html_url
                if head.startswith(branch_prefix) and num_token.search(head):  # devin/...-<num>
                    return pr.html_url
            print(f"[github] no open PR matched issue #{issue_number} (scanned {scanned})")
            return None
        except Exception as exc:
            print(f"[github] PR lookup failed for #{issue_number}: {exc}")
            return None

    def get_pr_state(self, pr_url: str) -> str | None:
        """Return 'merged' | 'open' | 'closed' for a PR url (live PR status), or None."""
        try:
            number = int(pr_url.rstrip("/").split("/")[-1])
            pr = self._repo.get_pull(number)
            if pr.merged:
                return "merged"
            return pr.state
        except Exception:
            return None
