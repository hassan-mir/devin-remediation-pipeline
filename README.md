# Calibrated Autonomy — Remediation Pipeline

An event-driven automation that uses [Devin](https://devin.ai) as a core primitive to clear
an engineering backlog — security findings, dependency CVEs, bugs, quality issues, flaky
tests — at a level of autonomy calibrated to the risk of each change.

The pipeline discovers candidate issues from pluggable channels, lets an operator **select**
which to act on, has Devin **triage** each one into a **HITL Requirement Score**, and then
**routes** it to the right autonomy tier — from "open a PR for review" to "escalate to a
human." A live dashboard reports both the trust signals and the throughput.

---

## Why this design

The hard part of an autonomous backlog is not writing each fix — it is deciding *which work
is safe to automate and how much human oversight each change needs*. Most coding-agent demos
ask "is the agent confident it can do this?" This pipeline asks a different question: **how
much human judgment does this change require?** That is the HITL (human-in-the-loop)
Requirement Score, and it drives a policy the orchestrator enforces — the agent proposes, the
policy disposes.

---

## How it works

```
┌─ Discovery channels ─────────────────────────────────────────────────────┐
│ pip-audit · bandit · github-issues · github-issue   (active)              │
│ ci-failure · sentry · datadog · slack    (planned — pluggable)            │
└───────────────┬──────────────────────────────────────────────────────────┘
                │  candidates  (status: discovered)
                ▼
        ╔═ Selection gate ═╗   an operator chooses which candidates to run on
        ╚════════┬═════════╝   (a single labelled GitHub issue is its own selection)
                 ▼
        Devin TRIAGE session  ──▶  HITL Requirement Score 0–100 + risk axes
                 │                  (posted to the GitHub issue and the dashboard)
                 ▼
        ╔═ Autonomy gate (policy: score → tier) ═══════════════╗
        ║  < 20 auto_merge     20–69 auto_pr                    ║
        ║  70–89 approve_first   ≥ 90 human_only                ║
        ╚════════┬═════════════════════════════════════════════╝
                 ▼
        Devin REMEDIATION session  ──▶  branch + fix + tests + PR + self-review
                 │
                 ▼
        Observability: dashboard (status, HITL, tiers, PRs, hours saved) + structured logs
```

There are **two decision points**:

1. **Selection gate** — which discovered candidates to process at all. Discovery can be noisy
   or large (hundreds of findings); selection keeps Devin focused and cost bounded.
2. **Autonomy gate** — how much oversight each selected task needs, computed by the policy
   from Devin's HITL score, not chosen by the agent.

Devin is the primitive in both phases: a **triage** session that analyses the change, and a
**remediation** session that fixes it. Both return a structured-output contract, so the
orchestrator works from machine-readable signals (score, risks, PR URL) rather than free text.

> Devin emits no outbound state webhooks, so session progress is tracked by polling. The
> "event-driven" trigger is the event *into* the pipeline (a labelled issue or a scan result).

---

## Entry points

Work enters the pipeline two ways, both flowing into the same triage → gate → remediation flow:

- **Label a GitHub issue** (event-driven) — a webhook on the labelled issue starts it immediately:
  - `devin-triage` — evaluate only: score the HITL requirement and post the report, then stop.
  - `devin-remediate` — calibrated: triage, then route by policy (auto-PR / approve-first / escalate).
  - `devin-fix` — skip triage and remediate directly (for issues already vetted by a human).
- **From the dashboard** (curated) — discover a batch, select what you want, and choose
  **Triage only** (evaluate) or **Run Devin** (remediate). The selection is your gate.

Either way remediation is still governed by the autonomy policy — labelling an issue does not
bypass the calibration unless you explicitly use `devin-fix`.

**Autonomous mode:** set `AUTONOMOUS_MODE` (or the dashboard toggle) to skip the selection gate
entirely — discovery flows straight into triage → remediation. The HITL policy still governs each
task, so only safe changes auto-fix; risky ones still escalate or wait for approval.

For repo-native triggering, copy `examples/github-actions-trigger.yml` into the target repo's
`.github/workflows/` and set a `PIPELINE_WEBHOOK_URL` secret; it forwards labelled-issue events
to the pipeline. The webhook and `scripts/simulate_event.py` work without it.

> **Hardening:** `/webhook/github` verifies an HMAC signature only when `GITHUB_WEBHOOK_SECRET`
> is set (unset is fine for a local demo, where the endpoint is unauthenticated). The bundled
> Actions forwarder posts unsigned, so turn the secret on only behind a forwarder that signs
> the payload.

## Discovery channels

| Channel | Kind | Status | Source of work |
|---|---|---|---|
| `pip-audit` | scan | active | Dependency CVEs (PyPI / OSV advisory database) |
| `bandit` | scan | active | Python static-analysis (SAST) findings |
| `github-issues` | scan | active | Open issues pulled from a configured source repo |
| `github-issue` | event | active | Any issue labelled `devin-triage` / `devin-remediate` / `devin-fix` |
| `ci-failure` | event | planned | Failed CI runs surfaced as fix candidates |
| `sentry` | event | planned | Recurring production errors |
| `datadog` | event | planned | Alerting thresholds breached |
| `slack` | event | planned | Engineer @-mentions the bot to file work |

Channels are registered in `app/discovery.py`. A new pull channel is a `DiscoverySource`
subclass; a new push channel is an API handler that builds a `Finding`. Everything downstream
is channel-agnostic.

---

## HITL Requirement Score and autonomy tiers

Triage scores how much oversight a change requires across five axes — **blast radius,
reversibility, verifiability, ambiguity, test coverage** — into a 0–100 score. The policy
(`app/policy.py`, thresholds in config) maps the score to a tier:

| HITL score | Tier | Behaviour |
|---|---|---|
| `< 20` | `auto_merge` | Fix, post a self-review on the PR, and auto-merge once it is mergeable (when `ENABLE_AUTO_MERGE` is on) |
| `20–69` | `auto_pr` | Fix and open a PR for human review (default) |
| `70–89` | `approve_first` | Triage report posted; a human approves before any code change |
| `≥ 90` | `human_only` | Devin posts an analysis only; no PR — escalated to a human |

---

## Project layout

```
app/
  config.py         Settings (Devin/GitHub creds, policy thresholds, hours-saved assumption)
  models.py         Finding, TriageAssessment, RemediationTask, lifecycle status enum
  discovery.py      Discovery channels: sources, registry (CHANNELS), GitHub-issue ingest
  scanner.py        pip-audit / bandit adapters -> Findings
  prompts.py        Triage and remediation prompts + structured-output schemas
  policy.py         HITL score -> autonomy tier (the control-plane decision)
  runtime.py        Operator-tunable knobs (thresholds, guardrails, hours-saved estimate, issue source), persisted
  orchestrator.py   discovery -> selection -> triage -> gate -> remediation; polling loop
  devin_client.py   Devin v3 API client (create / poll / structured output)
  github_client.py  Create labelled issues, comment triage reports, read & merge PRs
  store.py          SQLite state store (single source of truth for the dashboard)
  metrics.py        Trust + value roll-up for the dashboard
  main.py           FastAPI: triggers, selection/approval gates, webhook, dashboard, APIs
  dashboard/        Single-page observability dashboard
scripts/
  seed_issues.py    Run a discovery channel and register candidates
  simulate_event.py Post a synthetic labelled-issue webhook (event channel demo)
tests/              Unit tests (policy, metrics, discovery, store) + sample fixtures
```

---

## Configuration

The autonomy thresholds, cost guardrails, hours-saved assumption, and issue source are also
**editable at runtime** from the dashboard's Configuration panel (persisted to
`data/runtime_config.json`); the environment values below are the defaults. Secrets and
connection settings stay in the environment and are not runtime-tunable.

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `DEVIN_API_KEY` | Devin service-user key (`cog_…`) |
| `DEVIN_ORG_ID` | Devin organization id |
| `DEVIN_MODE` | `normal` / `fast` / `lite` / `ultra` |
| `MAX_ACU_PER_SESSION`, `MAX_ACU_TRIAGE` | Per-session compute guardrails |
| `HITL_AUTO_MERGE_BELOW`, `HITL_AUTO_PR_BELOW`, `HITL_APPROVE_FIRST_BELOW` | Autonomy policy thresholds |
| `ENABLE_AUTO_MERGE` | Whether the `auto_merge` tier may merge (default off) |
| `AUTONOMOUS_MODE` | Auto-start discovered candidates instead of manual selection (default off) |
| `GITHUB_TOKEN`, `GITHUB_REPO` | PAT and `owner/repo` of the target fork |
| `REMEDIATE_LABEL`, `TRIAGE_LABEL`, `FIX_NOW_LABEL` | Trigger labels: remediate / evaluate-only / skip-triage |
| `ISSUE_SOURCE_REPO`, `ISSUE_SOURCE_LABELS`, `ISSUE_SOURCE_NUMBERS`, `ISSUE_PULL_LIMIT` | `github-issues` channel: which issues to pull |
| `MAX_CONCURRENT_SESSIONS` | Cap on parallel Devin sessions |
| `ENGINEER_HOURS_PER_FIX` | Estimation assumption behind the "hours saved" figure |

`.env.example` lists every variable (including `DEVIN_BASE_URL`, `GITHUB_WEBHOOK_SECRET`,
`POLL_INTERVAL_SECONDS`, `DB_PATH`, `RUNTIME_CONFIG_PATH`).

---

## Setup

1. **Fork the target repository** into your GitHub account/org.
2. **Connect the fork to your Devin org** so Devin can open PRs: Devin → *Settings →
   Integrations → GitHub → Add Connection*, granting access to the fork.
3. Generate a **Devin service-user key** (Devin → *Settings → Service users*) and note your
   **org id**.
4. Create a **GitHub PAT** with `repo` scope on the fork.
5. `cp .env.example .env` and fill it in.

Verify Devin access:

```bash
curl -s -X POST "https://api.devin.ai/v3/organizations/$DEVIN_ORG_ID/sessions" \
  -H "Authorization: Bearer $DEVIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"prompt":"Reply READY then stop. Do not modify code.","max_acu_limit":1}'
```

---

## Running it

```bash
docker compose up --build        # or: pip install -r requirements.txt && uvicorn app.main:app --reload
# open http://localhost:8000
```

**Discover candidates, then select and run from the dashboard:**

```bash
# pull open issues from the configured source repo (defaults to the configured fork)
curl -X POST "http://localhost:8000/trigger/scan?mode=github"
# or scan a local checkout for vulnerabilities / SAST findings
python -m scripts.seed_issues --mode pip-audit --checkout /path/to/superset
python -m scripts.seed_issues --mode bandit    --checkout /path/to/superset
```

The dashboard has buttons for the GitHub-issues and sample channels, so you can discover,
tick the candidates to remediate, and click **Run Devin on N selected** without leaving the page.
To curate a specific set, set `ISSUE_SOURCE_NUMBERS` in `.env`.

**Event channel (a labelled GitHub issue):**

```bash
python -m scripts.simulate_event      # posts a synthetic `issues.labeled` webhook
# (in production, point a GitHub webhook at /webhook/github)
```

For a self-contained demo with no checkout, `python -m scripts.seed_issues --mode sample`
loads `tests/fixtures/sample_findings.json`.

---

## Observability

The dashboard (`/`) shows the active and planned discovery channels, the selection gate, and
for every issue: its HITL score (with the risk axes), the autonomy tier, lifecycle status,
links into the Devin triage and remediation sessions, the PR, and the self-review status.
Headline metrics cover both **trust** (how work was routed, how much escalated to humans) and
**throughput** (PRs opened, merged, and an estimate of engineer-hours saved). JSON is available at
`/api/tasks`, `/api/metrics`, and `/api/channels`.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/trigger/scan?mode=sample` | Run a scan channel; register candidates |
| `POST` | `/run` | Selection gate: `{task_ids, mode}` — mode `remediate` / `triage` / `fix` (default `remediate`) |
| `POST` | `/approve/{task_id}` | Autonomy gate: approve an `approve_first` task |
| `POST` | `/api/retry/{task_id}` | Re-run a `failed` task in a fresh session (resumes from the fix if triage succeeded) |
| `POST` | `/webhook/github` | Event intake for labelled issues |
| `GET` | `/api/tasks`, `/api/metrics`, `/api/channels` | Dashboard data |
| `GET` `POST` | `/api/config` | Read / update runtime-tunable settings |
| `GET` | `/`, `/architecture` | Observability dashboard and the one-page architecture diagram |
| `POST` | `/api/reset` | Clear all pipeline tasks (issues + config untouched) |

---

## Testing

```bash
pip install -r requirements.txt
pytest
```

---

## Extending

- **Add a discovery channel** — implement a `DiscoverySource` (pull) or an API handler that
  builds a `Finding` (push), and register it in `app/discovery.py::CHANNELS`.
- **Tune the autonomy policy** — adjust the `HITL_*` thresholds in `.env`, or the mapping in
  `app/policy.py`. Enable gated auto-merge with `ENABLE_AUTO_MERGE`.
- **Change the triage rubric** — edit the triage prompt and schema in `app/prompts.py`.

See `AGENTS.md` for an orientation written for AI agents working in this repository.
