"""Scanner wrappers: run pip-audit / bandit and normalise their JSON into Findings.

These are low-level tool adapters. The discovery layer composes them into channels.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .models import Finding, Severity

_SEVERITY_MAP = {
    "CRITICAL": Severity.critical, "HIGH": Severity.high, "MODERATE": Severity.medium,
    "MEDIUM": Severity.medium, "LOW": Severity.low,
}


def from_sample(path: str = "tests/fixtures/sample_findings.json") -> list[Finding]:
    return [Finding(**f) for f in json.loads(Path(path).read_text())]


def run_pip_audit(checkout_dir: str) -> list[Finding]:
    proc = subprocess.run(
        ["pip-audit", "-f", "json", "--progress-spinner", "off"],
        cwd=checkout_dir, capture_output=True, text=True,
    )
    findings: list[Finding] = []
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return findings
    for dep in payload.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            fix = (vuln.get("fix_versions") or ["latest"])[0]
            findings.append(Finding(
                id=f"pip-audit:{vuln['id']}:{dep['name']}",
                source="pip-audit",
                severity=Severity.high,
                title=f"{vuln['id']} in {dep['name']} {dep.get('version', '')}",
                description=vuln.get("description", ""),
                location=f"{dep['name']}=={dep.get('version', '')}",
                recommendation=f"Upgrade {dep['name']} to {fix}",
                raw=vuln,
            ))
    return findings


def run_bandit(checkout_dir: str) -> list[Finding]:
    proc = subprocess.run(
        ["bandit", "-r", ".", "-f", "json", "-q"],
        cwd=checkout_dir, capture_output=True, text=True,
    )
    findings: list[Finding] = []
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return findings
    for r in payload.get("results", []):
        findings.append(Finding(
            id=f"bandit:{r['test_id']}:{r['filename']}:{r['line_number']}",
            source="bandit",
            severity=_SEVERITY_MAP.get(r.get("issue_severity", "MEDIUM").upper(), Severity.medium),
            title=f"{r['test_id']} {r['test_name']}",
            description=r.get("issue_text", ""),
            location=f"{r['filename']}:{r['line_number']}",
            recommendation="Apply the secure pattern for this rule; preserve behaviour.",
            raw=r,
        ))
    return findings
