# AGENTS.md

Orientation for AI agents working in this repository. This describes the current state and
intent of the project — not its history.

## What this is

An event-driven automation that uses the Devin API to triage and remediate an engineering
backlog (security findings, dependency CVEs, bugs, quality issues, flaky tests). It runs Devin
at a level of autonomy calibrated to the risk of each change.

## Pipeline (the mental model)

```
discovery channels → selection gate → Devin triage → autonomy gate → Devin remediation → observability
```

- **Discovery** surfaces candidate `Finding`s from pluggable channels (scans and events).
- **Selection gate** — an operator chooses which candidates to act on (handles large backlogs).
  A single labelled GitHub issue is its own selection.
- **Triage** — a Devin session scores the change's **HITL Requirement Score** (0–100) across
  five risk axes and reports it to the issue and the dashboard.
- **Autonomy gate** — `app/policy.py` maps the score to a tier (`auto_merge`, `auto_pr`,
  `approve_first`, `human_only`). The policy decides; the agent only proposes.
- **Remediation** — a Devin session fixes the issue, adds tests, opens a PR, and self-reviews.
- **Observability** — SQLite-backed dashboard + JSON APIs.

**Entry points:** the dashboard selection gate (Triage only / Run Devin), or a labelled GitHub
issue — `devin-triage` (evaluate only), `devin-remediate` (calibrated), `devin-fix` (skip triage).
Each task carries a `run_mode` (`remediate` / `triage` / `fix`) that controls whether triage
proceeds to remediation.

## Invariants to preserve

- **Devin is the primitive; the orchestrator is thin.** Reasoning about code lives in Devin
  sessions (`app/prompts.py`), not in Python heuristics.
- **Autonomy policy lives in `app/policy.py`, never in prompts.** The triage agent reports a
  score; it must not be able to set its own tier.
- **The store is the single source of truth.** All task state goes through `app/store.py`;
  the dashboard and metrics read only from it.
- **Idempotency by finding id.** One `RemediationTask` per `Finding.id`; re-running a discovery
  channel or re-labelling an issue must not duplicate work.
- **Both Devin phases use structured-output contracts** (`TRIAGE_SCHEMA`, `REMEDIATION_SCHEMA`)
  so the orchestrator consumes machine-readable signals.
- **Session tracking is by polling.** Devin emits no outbound state webhooks; the background
  poller in `app/orchestrator.py` advances tasks.
- **Channels are pluggable and channel-agnostic downstream.** Register channels in
  `app/discovery.py::CHANNELS`.
- **Tunable knobs live in `app/runtime.py`** (autonomy thresholds, guardrails, hours-saved estimate, issue
  source), editable via `/api/config` and persisted; secrets and connection settings stay in
  `app/config.py` and are not runtime-tunable.

## Module map

| File | Responsibility |
|---|---|
| `app/config.py` | Settings from env (`.env`): credentials, connection, and tunable defaults |
| `app/runtime.py` | Operator-tunable knobs (thresholds, guardrails, hours-saved estimate, issue source), persisted to disk, editable via `/api/config` |
| `app/models.py` | `Finding`, `TriageAssessment`, `RemediationTask`, `TaskStatus` lifecycle |
| `app/discovery.py` | Channels: `DiscoverySource`s, `CHANNELS` registry, GitHub-issue ingest |
| `app/scanner.py` | pip-audit / bandit adapters |
| `app/prompts.py` | Triage + remediation prompts and schemas |
| `app/policy.py` | HITL score → autonomy tier |
| `app/orchestrator.py` | The pipeline state machine + polling loop |
| `app/devin_client.py` | Devin v3 API client |
| `app/github_client.py` | Issue creation, triage comments, PR state & auto-merge |
| `app/store.py` | SQLite persistence |
| `app/metrics.py` | Dashboard roll-up (trust + value) |
| `app/main.py` | FastAPI surface (triggers, gates, webhook, dashboard, APIs) |

## Lifecycle states

`discovered → queued → triaging → triaged → (auto_pr|auto_merge → remediating → in_review →
succeeded → merged*) | (approve_first → awaiting_approval → remediating …) | (human_only →
escalated)`; a triage-only run (`run_mode=triage`) stops at `triaged`; terminal failure is
`failed`. *`merged` only on the `auto_merge` tier when `ENABLE_AUTO_MERGE` is on. A `failed`
task is retryable (`/api/retry/{id}`): it re-runs in a fresh session, resuming from the fix
if a triage assessment already exists, otherwise restarting from triage.

## Run and test

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload     # dashboard at http://localhost:8000
pytest
```

Requires a `.env` (see `.env.example`): Devin service-user key + org id, a GitHub PAT, and the
target `owner/repo`. The Devin org must have the target repo connected via its GitHub App.

## Making common changes

- **Add a discovery channel:** implement a `DiscoverySource` (pull) or an API handler that
  builds a `Finding` (push); add it to `CHANNELS`.
- **Tune autonomy:** edit thresholds from the dashboard Configuration panel (or `HITL_*` in
  `.env` as defaults); the mapping is in `app/policy.py`.
- **Change triage scoring:** edit the triage prompt/schema in `app/prompts.py`.

## Conventions

- Python 3.11, FastAPI, Pydantic v2, SQLite, vanilla-JS dashboard.
- Comments explain the code as it is; keep them professional and current.
- Never commit `.env` or `data/` (see `.gitignore`).
