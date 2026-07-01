# AGENTS.md — `app/` package

Local orientation for the application package. See the repository-root `AGENTS.md` for the
full pipeline model and invariants.

## Call flow

```
main.py            HTTP surface; instantiates Store, DevinClient, GitHubClient, Orchestrator
  └─ orchestrator.py   the state machine
       ├─ discovery.py / scanner.py   produce Finding objects
       ├─ prompts.py                  build triage + remediation prompts (+ schemas)
       ├─ devin_client.py             create/poll Devin v3 sessions
       ├─ policy.py                   HITL score → autonomy tier
       ├─ github_client.py            issues, triage comments, PR state & merge
       └─ store.py                    persist RemediationTask (read by metrics.py + dashboard)
```

## Responsibilities, at a glance

- **Stateless logic:** `models.py`, `prompts.py`, plus `policy.py` and `metrics.py` (they read
  the in-memory `runtime` config but do no I/O at call time).
- **External I/O:** `devin_client.py` (Devin API), `github_client.py` (GitHub), `store.py` (SQLite),
  `scanner.py` (subprocess to pip-audit/bandit).
- **Coordination only:** `orchestrator.py` — it owns task transitions and the polling loop and
  holds no engineering heuristics of its own.
- **Config:** `config.py` (env, immutable) and `runtime.py` (operator-tunable, persisted to
  disk, editable via `/api/config`, read by `policy.py` / `orchestrator.py` / `metrics.py`).

## Conventions

- A `RemediationTask` is keyed by its `Finding.id`; that id is the idempotency key everywhere.
- `TaskStatus` is the lifecycle; only `orchestrator.py` advances it.
- Anything the dashboard shows must be derivable from `store.all()` via `metrics.py`.
- Keep the autonomy decision in `policy.py`; keep code-reasoning in Devin prompts.
