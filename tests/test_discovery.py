from app.discovery import SampleSource, finding_from_github_issue


def test_sample_source_loads_findings():
    findings = SampleSource().discover()
    assert len(findings) >= 1
    assert all(f.id and f.title for f in findings)


def test_finding_from_github_issue():
    f = finding_from_github_issue(42, "Crash on empty frame", "stacktrace here", {"bug"})
    assert f.source == "github-issue"
    assert f.id == "gh-issue:42"
    assert f.severity.value == "medium"
    assert f.description == "stacktrace here"


def test_finding_from_github_issue_severity_from_label():
    f = finding_from_github_issue(7, "Token leak", "", {"critical"})
    assert f.severity.value == "critical"
