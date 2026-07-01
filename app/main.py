"""HTTP surface: discovery triggers, the selection gate, the autonomy gate, event intake,
and the observability dashboard.

Run:  uvicorn app.main:app --reload   then open http://localhost:8000
"""
from __future__ import annotations

import hashlib
import hmac
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import discovery, metrics
from .config import settings
from .devin_client import DevinClient
from .github_client import GitHubClient
from .orchestrator import Orchestrator
from .runtime import runtime
from .store import Store

store = Store(settings.db_path)
devin = DevinClient(settings.devin_api_key, settings.devin_org_id, settings.devin_base_url)
github = GitHubClient(settings.github_token, settings.github_repo) if settings.github_token else None
orchestrator = Orchestrator(store, devin, github)

_FINDING_ID_RE = re.compile(r"finding-id:\s*([^\s>]+)")


class RunRequest(BaseModel):
    task_ids: list[str]
    mode: str = "remediate"   # remediate | triage | fix


@asynccontextmanager
async def lifespan(app: FastAPI):
    orchestrator.start_background_poller()
    yield


app = FastAPI(title="Calibrated Autonomy — Remediation Pipeline", lifespan=lifespan)


# --- discovery: scan channels seed candidates (no Devin runs yet) ---
@app.post("/trigger/scan")
def trigger_scan(mode: str = "sample"):
    """Run a discovery channel and register candidates as `discovered` (awaiting selection)."""
    if mode == "sample":
        findings = discovery.SampleSource().discover()
    elif mode == "github":
        labels = [s.strip() for s in runtime.issue_source_labels.split(",") if s.strip()]
        numbers = [int(s) for s in runtime.issue_source_numbers.split(",") if s.strip().isdigit()]
        findings = discovery.GitHubIssueSource(
            runtime.issue_source_repo, settings.github_token, labels, numbers,
            runtime.issue_pull_limit).discover()
    else:
        raise HTTPException(400, "supported modes: sample, github (pip-audit/bandit via scripts/seed_issues.py)")
    tasks = orchestrator.seed(findings)
    return {"discovered": len(tasks), "mode": mode}


# --- selection gate: operator chooses which candidates to run Devin on ---
@app.post("/run")
def run(req: RunRequest):
    tasks = orchestrator.run(req.task_ids, req.mode)
    return {"started": [t.task_id for t in tasks], "mode": req.mode}


# --- autonomy gate: approve an `approve_first` task to proceed to remediation ---
@app.post("/approve/{task_id}")
def approve(task_id: str):
    task = orchestrator.approve(task_id)
    return {"task": task_id, "status": task.status}


# --- event intake: any labelled GitHub issue (the label is its own selection) ---
@app.post("/webhook/github")
async def github_webhook(request: Request,
                         x_hub_signature_256: str | None = Header(default=None)):
    body = await request.body()
    if settings.github_webhook_secret:
        _verify_signature(body, x_hub_signature_256)
    payload = await request.json()

    if payload.get("action") not in {"labeled", "opened"}:
        return {"ignored": "action"}
    issue = payload.get("issue", {})
    labels = {l["name"] for l in issue.get("labels", [])}

    if settings.fix_now_label in labels:
        mode = "fix"
    elif settings.remediate_label in labels:
        mode = "remediate"
    elif settings.triage_label in labels:
        mode = "triage"
    else:
        return {"ignored": "label"}

    finding_id = _extract_finding_id(issue.get("body", ""))
    if finding_id and store.get(finding_id):
        task_id = finding_id                              # scanner-seeded issue
    else:                                                 # arbitrary filed issue
        finding = discovery.finding_from_github_issue(
            issue.get("number", 0), issue.get("title", ""), issue.get("body", ""), labels)
        orchestrator.ingest_issue(finding, issue.get("number"), issue.get("html_url"))
        task_id = finding.id

    task = orchestrator.start(task_id, mode)
    return {"action": mode, "task": task.task_id, "status": task.status}


# --- observability ---
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (Path(__file__).parent / "dashboard" / "index.html").read_text()


@app.get("/architecture", response_class=HTMLResponse)
def architecture():
    """One-page architecture diagram (browser tab, or Save-as-PDF for a slide)."""
    return (Path(__file__).parent / "dashboard" / "architecture.html").read_text()


@app.get("/api/tasks")
def api_tasks():
    return JSONResponse([t.model_dump(mode="json") for t in store.all()])


@app.get("/api/metrics")
def api_metrics():
    return metrics.summarise(store.all())


@app.get("/api/channels")
def api_channels():
    return discovery.CHANNELS


# --- runtime configuration ---
@app.get("/api/config")
def get_config():
    return runtime.as_dict()


@app.post("/api/config")
async def set_config(request: Request):
    patch = await request.json()
    return runtime.update(patch)


@app.post("/api/reset")
def reset():
    """Clear all pipeline tasks (leaves the GitHub issues and config untouched)."""
    orchestrator.reset()
    return {"cleared": True}


@app.post("/api/retry/{task_id}")
def retry(task_id: str):
    """Re-run a failed task in a fresh Devin session (resumes from the fix if triage succeeded)."""
    task = orchestrator.retry(task_id)
    return {"task": task_id, "status": task.status}


# --- helpers ---
def _extract_finding_id(body: str) -> str | None:
    m = _FINDING_ID_RE.search(body or "")
    return m.group(1) if m else None


def _verify_signature(body: bytes, signature: str | None) -> None:
    if not signature:
        raise HTTPException(401, "missing signature")
    digest = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, signature):
        raise HTTPException(401, "bad signature")
