from __future__ import annotations

import json
from typing import Any, Callable

from harness.policy import PolicyExecutor
from harness.snapshot import collect_snapshot

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_snapshot",
            "description": "Re-read cheap baseline host metrics (load, memory, disk, failed units, patch-check, journal errors).",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_inspect",
            "description": "Run an allowlisted read-only command. Pass argv as an array (no shell). Examples: journalctl -n 50 -p err --no-pager; ss -lntup; systemctl status sshd.service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Argument vector, e.g. [\"journalctl\", \"-n\", \"30\", \"--no-pager\"]",
                    }
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "service_ctl",
            "description": "systemctl status/restart/start/stop for a unit on ALLOWED_UNITS. restart/start/stop are mutating.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["status", "restart", "start", "stop", "is-failed", "is-active"],
                    },
                },
                "required": ["unit", "action"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file under ALLOWED_PATHS.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a text file under ALLOWED_PATHS. Creates a backup first. Mutating.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "zypper",
            "description": "OpenSUSE zypper: patch-check, list-patches, lu (inspect) or patch (mutating, requires ALLOW_PATCHES).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["patch-check", "list-patches", "lu", "patch"],
                    }
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_discord",
            "description": "Send an extra mid-window Discord alert. Use only for crit situations that should not wait for the final report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["headline", "detail"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_finding",
            "description": "Store a structured finding in this window's memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["ok", "warn", "crit"]},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["severity", "title", "detail"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_report",
            "description": "Finish the window with a structured report. Call this once when done inspecting and acting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["ok", "warn", "crit"]},
                    "headline": {"type": "string"},
                    "findings": {"type": "array", "items": {"type": "string"}},
                    "actions": {"type": "array", "items": {"type": "string"}},
                    "next_focus": {"type": "string"},
                },
                "required": ["severity", "headline", "findings", "actions", "next_focus"],
                "additionalProperties": False,
            },
        },
    },
]


def parse_tool_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class ToolDispatcher:
    def __init__(
        self,
        executor: PolicyExecutor,
        notify_discord: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.executor = executor
        self.notify_discord = notify_discord
        self.report: dict[str, Any] | None = None

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_snapshot":
            return {"ok": True, "snapshot": collect_snapshot()}
        if name == "run_inspect":
            argv = arguments.get("argv") or []
            if isinstance(argv, str):
                argv = argv.split()
            return self.executor.run_inspect([str(x) for x in argv])
        if name == "service_ctl":
            return self.executor.service_ctl(str(arguments.get("unit", "")), str(arguments.get("action", "")))
        if name == "read_file":
            return self.executor.read_file(str(arguments.get("path", "")))
        if name == "write_file":
            return self.executor.write_file(
                str(arguments.get("path", "")),
                str(arguments.get("content", "")),
            )
        if name == "zypper":
            return self.executor.zypper(str(arguments.get("action", "")))
        if name == "notify_discord":
            if not self.notify_discord:
                return {"ok": False, "error": "discord notifier not configured"}
            return self.notify_discord(
                str(arguments.get("headline", "alert")),
                str(arguments.get("detail", "")),
            )
        if name == "record_finding":
            return self.executor.record_finding(
                str(arguments.get("severity", "warn")),
                str(arguments.get("title", "")),
                str(arguments.get("detail", "")),
            )
        if name == "submit_report":
            self.report = {
                "severity": arguments.get("severity", "warn"),
                "headline": arguments.get("headline", ""),
                "findings": arguments.get("findings") or [],
                "actions": arguments.get("actions") or [],
                "next_focus": arguments.get("next_focus", ""),
            }
            return {"ok": True, "accepted": True}
        return {"ok": False, "error": f"unknown tool: {name}"}
