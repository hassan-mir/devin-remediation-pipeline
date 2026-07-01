"""Thin, faithful client for the Devin v3 (organization-scoped) API.

This is the *plumbing* — it deliberately knows nothing about findings, issues, or
remediation policy. It just speaks the Devin API. The orchestrator decides WHAT to
send; this decides HOW to send it.

Endpoints (v3, org-scoped):
  POST   /v3/organizations/{org_id}/sessions
  GET    /v3/organizations/{org_id}/sessions/{session_id}

Docs: https://docs.devin.ai/api-reference/v3/usage-examples
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

# Statuses treated as terminal (the session has stopped for good). Completion is primarily
# detected from the structured output the session produces; this is the fallback. "suspended"
# is intentionally excluded: Devin uses it for transient, resumable pauses, so treating it as
# terminal would fail a session that is still working.
TERMINAL_STATUSES = {"exit", "error", "finished"}


class DevinClient:
    def __init__(self, api_key: str, org_id: str, base_url: str = "https://api.devin.ai/v3",
                 timeout: float = 30.0):
        # Construction never fails, so the dashboard boots for inspection without credentials.
        # A missing key/org only errors when a real Devin API call is made (guarded in _request).
        self._api_key, self._org_id = api_key, org_id
        self._org_base = f"{base_url.rstrip('/')}/organizations/{org_id}"
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    # --- internal: request with simple backoff on 429 / 5xx ---
    def _request(self, method: str, path: str, *, json: Optional[dict] = None,
                 params: Optional[dict] = None, retries: int = 4) -> dict:
        if not self._api_key or not self._org_id:
            raise RuntimeError("DEVIN_API_KEY and DEVIN_ORG_ID must be set to use the Devin API")
        url = f"{self._org_base}{path}"
        delay = 2.0
        for attempt in range(retries + 1):
            resp = self._client.request(method, url, json=json, params=params)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        resp.raise_for_status()  # pragma: no cover
        return {}

    # --- create ---
    def create_session(self, prompt: str, *, repos: Optional[list[str]] = None,
                       tags: Optional[list[str]] = None, max_acu_limit: Optional[int] = None,
                       devin_mode: str = "normal",
                       structured_output_schema: Optional[dict] = None,
                       title: Optional[str] = None) -> dict:
        """Create a Devin session. Returns {session_id, url, status, ...}.

        NOTE: v3 does not document an `idempotent` flag (v1 did). We dedupe
        client-side in the store instead — see orchestrator.
        """
        body: dict[str, Any] = {"prompt": prompt, "devin_mode": devin_mode}
        if repos:
            body["repos"] = repos
        if tags:
            body["tags"] = tags
        if max_acu_limit:
            body["max_acu_limit"] = max_acu_limit
        if title:
            body["title"] = title
        if structured_output_schema:
            body["structured_output_required"] = True
            body["structured_output_schema"] = structured_output_schema
        return self._request("POST", "/sessions", json=body)

    # --- read ---
    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/sessions/{session_id}")

    # --- helpers ---
    @staticmethod
    def is_terminal(status: Optional[str]) -> bool:
        return status in TERMINAL_STATUSES

    @staticmethod
    def extract_pr_url(session: dict) -> Optional[str]:
        # v3 returns PRs in pull_requests[].pr_url; structured_output may also carry it.
        prs = session.get("pull_requests") or []
        if prs:
            return prs[0].get("pr_url")
        out = session.get("structured_output") or {}
        return out.get("pr_url")
