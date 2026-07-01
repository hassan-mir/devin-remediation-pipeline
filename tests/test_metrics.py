from app.metrics import summarise
from app.models import Finding, RemediationTask, Severity, TaskStatus
from app.runtime import runtime


def _finding(i: str) -> Finding:
    return Finding(id=i, source="sample", severity=Severity.high, title=i, description="d")


def test_summarise_counts_and_value():
    tasks = [
        RemediationTask(task_id="a", finding=_finding("a"), status=TaskStatus.succeeded,
                        pr_url="https://example/pr/1", acus_consumed=3.0),
        RemediationTask(task_id="b", finding=_finding("b"), status=TaskStatus.succeeded,
                        pr_url="https://example/pr/2", acus_consumed=3.0),
        RemediationTask(task_id="c", finding=_finding("c"), status=TaskStatus.escalated),
        RemediationTask(task_id="d", finding=_finding("d"), status=TaskStatus.discovered),
    ]
    m = summarise(tasks)
    assert m["total_tasks"] == 4
    assert m["candidates"] == 1
    assert m["selected"] == 3
    assert m["prs_opened"] == 2
    assert m["escalated_to_human"] == 1
    assert m["engineer_hours_saved"] == 2 * runtime.engineer_hours_per_fix
