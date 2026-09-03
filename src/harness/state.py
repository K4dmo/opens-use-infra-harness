from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_state() -> dict[str, Any]:
    return {
        "consecutive_failures": 0,
        "circuit_open": False,
        "last_snapshot": {},
        "last_report": {},
        "last_actions": [],
        "findings": [],
        "last_run_at": None,
        "last_discord_at": None,
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    merged = empty_state()
    merged.update(data)
    return merged


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["saved_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def memory_for_prompt(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "consecutive_failures": state.get("consecutive_failures", 0),
        "circuit_open": state.get("circuit_open", False),
        "last_run_at": state.get("last_run_at"),
        "last_report": state.get("last_report") or {},
        "last_actions": state.get("last_actions") or [],
        "prior_findings": (state.get("findings") or [])[-20:],
    }
