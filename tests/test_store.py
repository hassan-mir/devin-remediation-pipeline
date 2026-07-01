from app.models import (AutonomyTier, Finding, RemediationTask, Severity, TaskStatus,
                        TriageAssessment)
from app.store import Store


def test_store_roundtrip(tmp_path):
    store = Store(str(tmp_path / "pipeline.db"))
    finding = Finding(id="x", source="sample", severity=Severity.high, title="t", description="d")
    task = RemediationTask(
        task_id="x", finding=finding, status=TaskStatus.triaged,
        triage=TriageAssessment(hitl_score=30, recommended_tier=AutonomyTier.auto_pr,
                                rationale="r", risks=["one risk"]),
    )
    store.upsert(task)

    got = store.get("x")
    assert got is not None
    assert got.finding.title == "t"
    assert got.status == TaskStatus.triaged
    assert got.hitl_score == 30
    assert got.tier == "auto_pr"
    assert got.triage.risks == ["one risk"]


def test_store_idempotent_upsert(tmp_path):
    store = Store(str(tmp_path / "pipeline.db"))
    finding = Finding(id="y", source="sample", severity=Severity.low, title="t", description="d")
    store.upsert(RemediationTask(task_id="y", finding=finding))
    store.upsert(RemediationTask(task_id="y", finding=finding, status=TaskStatus.succeeded))
    assert len(store.all()) == 1
    assert store.get("y").status == TaskStatus.succeeded
