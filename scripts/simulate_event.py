"""Simulate the event-driven discovery channel: a labelled GitHub issue.

Posts a synthetic GitHub `issues.labeled` webhook to the running app, exactly as GitHub
would when an engineer labels an issue with `devin-remediate`. The pipeline turns the issue
into a Finding and triages it — demonstrating that any filed issue can be ingested, not just
scanner output.

  python -m scripts.simulate_event
"""
from __future__ import annotations

import httpx

from app.config import settings

APP = "http://localhost:8000"

SAMPLE_ISSUE = {
    "number": 90001,
    "title": "Chart export raises on an empty DataFrame instead of returning a 400",
    "body": "Exporting a chart whose query yields no rows raises an unhandled exception. "
            "It should fail gracefully with a 400 and a clear message.",
    "html_url": "https://github.com/hassan-mir/superset/issues/90001",
    "labels": [{"name": settings.remediate_label}, {"name": "bug"}],
}


def main() -> None:
    payload = {"action": "labeled", "issue": SAMPLE_ISSUE}
    r = httpx.post(f"{APP}/webhook/github", json=payload, timeout=60)
    print(f"webhook -> {r.status_code} {r.json()}")


if __name__ == "__main__":
    main()
