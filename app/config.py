"""Central configuration, loaded from environment / .env.

Everything secret or environment-specific lives here so the rest of the code
never reads os.environ directly.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Devin API (v3, organization-scoped) ---
    devin_api_key: str = ""                       # service-user key, "cog_..."
    devin_org_id: str = ""                        # organization id, e.g. "hassan-mir-devin-demo"
    devin_base_url: str = "https://api.devin.ai/v3"
    devin_mode: str = "normal"                    # normal | fast | lite | ultra
    max_acu_per_session: int = 5                  # remediation cost guardrail
    max_acu_triage: int = 2                        # triage is cheaper (read-only analysis)

    # --- Calibrated-Autonomy policy (HITL thresholds; the orchestrator, not the agent, decides) ---
    hitl_auto_merge_below: int = 20               # < 20  -> auto_merge tier
    hitl_auto_pr_below: int = 70                  # 20-69 -> auto_pr (PR + human review)
    hitl_approve_first_below: int = 90            # 70-89 -> approve_first; >=90 -> human_only
    enable_auto_merge: bool = False               # even auto_merge tier only opens a PR unless True
    autonomous_mode: bool = False                 # discovery runs straight to remediation (skip manual selection)

    # --- GitHub ---
    github_token: str = ""                        # optional PAT: issue comments, scan-created issues, auto-merge
    github_repo: str = "hassan-mir/superset"      # owner/repo the pipeline acts on
    remediate_label: str = "devin-remediate"      # triage -> policy gate -> remediate
    triage_label: str = "devin-triage"            # evaluate only (triage, no remediation)
    fix_now_label: str = "devin-fix"              # skip triage, remediate directly
    github_webhook_secret: str = ""               # optional: verify webhook signatures

    # GitHub issue discovery (pull real issues from an upstream repo for triage)
    issue_source_repo: str = "hassan-mir/superset"  # where to pull candidate issues from
    issue_source_labels: str = ""                 # comma-separated label filter, optional
    issue_source_numbers: str = ""                # comma-separated specific issue numbers, optional
    issue_pull_limit: int = 8

    # --- Orchestrator behaviour ---
    max_concurrent_sessions: int = 8              # Pro allows up to 10 concurrent
    poll_interval_seconds: int = 10

    # --- Estimation assumption (drives the "hours saved" figure) ---
    engineer_hours_per_fix: float = 3.0           # baseline a human would spend per finding

    # --- Storage ---
    db_path: str = "data/pipeline.db"
    runtime_config_path: str = "data/runtime_config.json"   # operator-tunable overrides


settings = Settings()
