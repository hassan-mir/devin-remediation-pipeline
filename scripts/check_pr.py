"""Diagnostic: show open PRs and whether the pipeline can match one to an issue.

Run from the repo root in the same environment the server uses:
    python scripts/check_pr.py <issue_number>

It reads GITHUB_TOKEN / GITHUB_REPO from .env (the token is never printed) and prints
the open PRs with their branch names, then whether find_open_pr would recover one.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import settings
from app.github_client import GitHubClient


def main() -> None:
    issue = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    if not settings.github_token:
        print("GITHUB_TOKEN is not set, so the pipeline cannot query GitHub and cannot "
              "recover a PR. Set it in .env and restart.")
        return
    gh = GitHubClient(settings.github_token, settings.github_repo)
    print(f"repo: {settings.github_repo}")
    print("open PRs (newest first):")
    found_any = False
    for pr in gh._repo.get_pulls(state="open", sort="created", direction="desc")[:20]:
        found_any = True
        print(f"  #{pr.number}  head={pr.head.ref!r}  title={pr.title!r}")
    if not found_any:
        print("  (none)")
    if issue is not None:
        print(f"\nfind_open_pr(#{issue}) -> {gh.find_open_pr(issue) or 'NO MATCH'}")
    else:
        print("\nPass an issue number to test matching, e.g. python -m scripts.check_pr 1")


if __name__ == "__main__":
    main()
