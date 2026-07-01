"""Operator-tunable configuration, editable at runtime and persisted to disk.

Seeded from environment defaults (`config.settings`) at startup. The pipeline reads its
tunable knobs (autonomy thresholds, concurrency, cost guardrails, hours-saved assumptions, and the
issue source) from here, so they can be changed from the dashboard without a restart.
Secrets and connection settings stay in `config.settings` and are not tunable here.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import settings

TUNABLE = (
    "hitl_auto_merge_below", "hitl_auto_pr_below", "hitl_approve_first_below", "enable_auto_merge",
    "autonomous_mode",
    "max_concurrent_sessions", "max_acu_per_session", "max_acu_triage", "devin_mode",
    "poll_interval_seconds",
    "engineer_hours_per_fix",
    "issue_source_repo", "issue_source_labels", "issue_source_numbers", "issue_pull_limit",
)


def _coerce(current, new):
    if isinstance(current, bool):
        return new if isinstance(new, bool) else str(new).strip().lower() in ("1", "true", "on", "yes")
    if isinstance(current, int):
        try:
            return int(new)
        except (TypeError, ValueError):
            return current
    if isinstance(current, float):
        try:
            return float(new)
        except (TypeError, ValueError):
            return current
    return "" if new is None else str(new)


class RuntimeConfig:
    def __init__(self, path: str):
        object.__setattr__(self, "_path", Path(path))
        values = {k: getattr(settings, k) for k in TUNABLE}
        p = Path(path)
        if p.exists():
            try:
                saved = json.loads(p.read_text())
                values.update({k: saved[k] for k in TUNABLE if k in saved})
            except Exception:
                pass
        object.__setattr__(self, "_values", values)

    def __getattr__(self, name):
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        raise AttributeError(name)

    def as_dict(self) -> dict:
        return dict(object.__getattribute__(self, "_values"))

    def update(self, patch: dict) -> dict:
        values = object.__getattribute__(self, "_values")
        for key, value in patch.items():
            if key in values:
                values[key] = _coerce(values[key], value)
        path = object.__getattribute__(self, "_path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, indent=2))
        return self.as_dict()


runtime = RuntimeConfig(settings.runtime_config_path)
