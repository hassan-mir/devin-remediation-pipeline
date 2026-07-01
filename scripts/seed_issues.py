"""Run a discovery channel against a target and register candidates (no Devin runs).

  python -m scripts.seed_issues --mode sample
  python -m scripts.seed_issues --mode pip-audit --checkout /path/to/superset
  python -m scripts.seed_issues --mode bandit --checkout /path/to/superset

Candidates land in the `discovered` state. Select and run them from the dashboard.
"""
from __future__ import annotations

import argparse

from app import discovery
from app.config import settings
from app.devin_client import DevinClient
from app.github_client import GitHubClient
from app.orchestrator import Orchestrator
from app.store import Store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=list(discovery.PULL_SOURCES), default="sample")
    ap.add_argument("--checkout", help="path to a checkout (pip-audit / bandit modes)")
    ap.add_argument("--limit", type=int, default=20, help="cap candidates registered")
    args = ap.parse_args()

    if args.mode == "sample":
        source = discovery.SampleSource()
    else:
        if not args.checkout:
            raise SystemExit(f"--checkout is required in {args.mode} mode")
        source = discovery.PULL_SOURCES[args.mode](args.checkout)

    findings = source.discover()[: args.limit]
    print(f"Discovered {len(findings)} candidates via '{args.mode}'.")

    store = Store(settings.db_path)
    devin = DevinClient(settings.devin_api_key, settings.devin_org_id, settings.devin_base_url)
    github = GitHubClient(settings.github_token, settings.github_repo) if settings.github_token else None
    orch = Orchestrator(store, devin, github)

    for t in orch.seed(findings):
        print(f"  {t.task_id} -> issue {t.issue_url or '(no github configured)'}")
    print("Select and run these from the dashboard at http://localhost:8000")


if __name__ == "__main__":
    main()
