"""SQLite state store — single source of truth for the dashboard.

One row per finding (task_id) => idempotency. Triage is stored as JSON, with
hitl_score / tier denormalised into columns for easy dashboard + metrics queries.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Finding, RemediationTask, TaskStatus, TriageAssessment

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id                 TEXT PRIMARY KEY,
    finding_json            TEXT NOT NULL,
    issue_number            INTEGER,
    issue_url               TEXT,
    status                  TEXT NOT NULL,
    run_mode                TEXT DEFAULT 'remediate',
    triage_session_id       TEXT,
    triage_session_url      TEXT,
    triage_json             TEXT,
    hitl_score              INTEGER,
    tier                    TEXT,
    remediation_session_id  TEXT,
    remediation_session_url TEXT,
    pr_url                  TEXT,
    review_status           TEXT,
    acus_consumed           REAL DEFAULT 0,
    summary                 TEXT DEFAULT '',
    error                   TEXT DEFAULT '',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        # tolerate stores created before columns were added
        try:
            self._conn.execute("ALTER TABLE tasks ADD COLUMN run_mode TEXT DEFAULT 'remediate'")
        except sqlite3.OperationalError:
            pass
        self._conn.commit()

    def upsert(self, task: RemediationTask) -> None:
        task.updated_at = datetime.utcnow()
        self._conn.execute(
            """INSERT INTO tasks (task_id, finding_json, issue_number, issue_url, status, run_mode,
                   triage_session_id, triage_session_url, triage_json, hitl_score, tier,
                   remediation_session_id, remediation_session_url, pr_url, review_status,
                   acus_consumed, summary, error, created_at, updated_at)
               VALUES (:task_id, :finding_json, :issue_number, :issue_url, :status, :run_mode,
                   :triage_session_id, :triage_session_url, :triage_json, :hitl_score, :tier,
                   :remediation_session_id, :remediation_session_url, :pr_url, :review_status,
                   :acus_consumed, :summary, :error, :created_at, :updated_at)
               ON CONFLICT(task_id) DO UPDATE SET
                   issue_number=excluded.issue_number, issue_url=excluded.issue_url,
                   status=excluded.status, run_mode=excluded.run_mode,
                   triage_session_id=excluded.triage_session_id,
                   triage_session_url=excluded.triage_session_url,
                   triage_json=excluded.triage_json, hitl_score=excluded.hitl_score,
                   tier=excluded.tier,
                   remediation_session_id=excluded.remediation_session_id,
                   remediation_session_url=excluded.remediation_session_url,
                   pr_url=excluded.pr_url, review_status=excluded.review_status,
                   acus_consumed=excluded.acus_consumed, summary=excluded.summary,
                   error=excluded.error, updated_at=excluded.updated_at""",
            {
                "task_id": task.task_id,
                "finding_json": task.finding.model_dump_json(),
                "issue_number": task.issue_number,
                "issue_url": task.issue_url,
                "status": task.status.value,
                "run_mode": task.run_mode,
                "triage_session_id": task.triage_session_id,
                "triage_session_url": task.triage_session_url,
                "triage_json": task.triage.model_dump_json() if task.triage else None,
                "hitl_score": task.hitl_score,
                "tier": task.tier,
                "remediation_session_id": task.remediation_session_id,
                "remediation_session_url": task.remediation_session_url,
                "pr_url": task.pr_url,
                "review_status": task.review_status,
                "acus_consumed": task.acus_consumed,
                "summary": task.summary,
                "error": task.error,
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
            },
        )
        self._conn.commit()

    def get(self, task_id: str) -> Optional[RemediationTask]:
        row = self._conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def all(self) -> list[RemediationTask]:
        rows = self._conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
        return [self._row_to_task(r) for r in rows]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM tasks")
        self._conn.commit()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> RemediationTask:
        triage = TriageAssessment(**json.loads(row["triage_json"])) if row["triage_json"] else None
        return RemediationTask(
            task_id=row["task_id"],
            finding=Finding(**json.loads(row["finding_json"])),
            issue_number=row["issue_number"],
            issue_url=row["issue_url"],
            status=TaskStatus(row["status"]),
            run_mode=row["run_mode"] or "remediate",
            triage_session_id=row["triage_session_id"],
            triage_session_url=row["triage_session_url"],
            triage=triage,
            remediation_session_id=row["remediation_session_id"],
            remediation_session_url=row["remediation_session_url"],
            pr_url=row["pr_url"],
            review_status=row["review_status"],
            acus_consumed=row["acus_consumed"] or 0.0,
            summary=row["summary"] or "",
            error=row["error"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
