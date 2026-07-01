"""Discovery channels — the pluggable sources that feed work into the pipeline.

Pull channels (scans) implement DiscoverySource and run on demand or on a schedule.
Push channels (events) arrive at the API layer; for example a labelled GitHub issue is
converted into a Finding by ``finding_from_github_issue``.

``CHANNELS`` is the registry of every channel — active or planned — and is surfaced on
the dashboard. New channels are added by implementing a source (pull) or an API handler
(push) and registering it here; the rest of the pipeline is channel-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from . import scanner
from .models import Finding, Severity


class DiscoverySource(ABC):
    name: str = "source"
    kind: str = "scan"

    @abstractmethod
    def discover(self) -> list[Finding]:
        ...


class SampleSource(DiscoverySource):
    name = "sample"

    def __init__(self, path: str = "tests/fixtures/sample_findings.json"):
        self._path = path

    def discover(self) -> list[Finding]:
        return scanner.from_sample(self._path)


class PipAuditSource(DiscoverySource):
    name = "pip-audit"

    def __init__(self, checkout_dir: str):
        self._dir = checkout_dir

    def discover(self) -> list[Finding]:
        return scanner.run_pip_audit(self._dir)


class BanditSource(DiscoverySource):
    name = "bandit"

    def __init__(self, checkout_dir: str):
        self._dir = checkout_dir

    def discover(self) -> list[Finding]:
        return scanner.run_bandit(self._dir)


class GitHubIssueSource(DiscoverySource):
    """Pull existing open issues from a configured source repo as candidates."""
    name = "github-issues"

    def __init__(self, repo: str, token: str = "", labels: list[str] | None = None,
                 numbers: list[int] | None = None, limit: int = 8):
        self._repo_name = repo
        self._token = token
        self._labels = labels or []
        self._numbers = numbers or []
        self._limit = limit

    def discover(self) -> list[Finding]:
        from github import Github

        gh = Github(self._token) if self._token else Github()
        repo = gh.get_repo(self._repo_name)
        findings: list[Finding] = []
        if self._numbers:
            for number in self._numbers:
                try:
                    issue = repo.get_issue(number)
                except Exception:
                    continue
                if issue.pull_request is None:
                    findings.append(self._to_finding(issue))
        else:
            kwargs: dict = {"state": "open"}
            if self._labels:
                kwargs["labels"] = self._labels
            for issue in repo.get_issues(**kwargs):
                if issue.pull_request is not None:
                    continue
                findings.append(self._to_finding(issue))
                if len(findings) >= self._limit:
                    break
        return findings

    def _to_finding(self, issue) -> Finding:
        labels = {l.name for l in issue.labels}
        severity = Severity.unknown
        for label in labels:
            if label.lower() in _LABEL_SEVERITY:
                severity = _LABEL_SEVERITY[label.lower()]
                break
        body = (issue.body or "").strip()
        return Finding(
            id=f"gh-issue:{issue.number}",
            source="github-issues",
            severity=severity,
            title=issue.title,
            description=body[:1500] or issue.title,
            location=f"{self._repo_name}#{issue.number}",
            raw={"repo": self._repo_name, "issue_number": issue.number, "issue_url": issue.html_url},
        )


PULL_SOURCES: dict[str, type[DiscoverySource]] = {
    "sample": SampleSource,
    "pip-audit": PipAuditSource,
    "bandit": BanditSource,
}

# Every channel the pipeline knows about. `active` channels are wired; `planned`
# channels document the extension surface and render as such on the dashboard.
CHANNELS: list[dict] = [
    {"name": "pip-audit", "kind": "scan", "status": "active",
     "description": "Dependency CVEs via the PyPI / OSV advisory database"},
    {"name": "bandit", "kind": "scan", "status": "active",
     "description": "Python static-analysis (SAST) findings"},
    {"name": "github-issues", "kind": "scan", "status": "active",
     "description": "Open issues pulled from a configured source repo"},
    {"name": "github-issue", "kind": "event", "status": "active",
     "description": "Any GitHub issue labelled for remediation"},
    {"name": "ci-failure", "kind": "event", "status": "planned",
     "description": "Failed CI runs surfaced as fix candidates"},
    {"name": "sentry", "kind": "event", "status": "planned",
     "description": "Recurring production errors"},
    {"name": "datadog", "kind": "event", "status": "planned",
     "description": "Alerting thresholds breached"},
    {"name": "slack", "kind": "event", "status": "planned",
     "description": "Engineer @-mentions the bot to file work"},
]

_LABEL_SEVERITY = {
    "critical": Severity.critical, "security": Severity.high, "high": Severity.high,
    "bug": Severity.medium, "medium": Severity.medium, "low": Severity.low,
}


def finding_from_github_issue(number: int, title: str, body: str, labels: set[str]) -> Finding:
    """Turn an arbitrary GitHub issue into a Finding, so any filed issue can be triaged."""
    severity = Severity.unknown
    for label in labels:
        if label.lower() in _LABEL_SEVERITY:
            severity = _LABEL_SEVERITY[label.lower()]
            break
    return Finding(
        id=f"gh-issue:{number}",
        source="github-issue",
        severity=severity,
        title=title,
        description=body or title,
    )
