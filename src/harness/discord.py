from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from harness.config import Config

COLORS = {
    "ok": 0x3BA55D,
    "warn": 0xFAA61A,
    "crit": 0xED4245,
    "error": 0x992D22,
}

SEVERITY_EMOJI = {"ok": "OK", "warn": "WARN", "crit": "CRIT", "error": "ERROR"}


def _clip(text: str, limit: int) -> str:
    text = text.strip() if text else ""
    if len(text) <= limit:
        return text or "-"
    return text[: limit - 3] + "..."


def _field(name: str, value: str, inline: bool = True) -> dict[str, Any]:
    return {"name": name[:256], "value": _clip(value, 1024), "inline": inline}


def _next_check(interval_seconds: int) -> str:
    when = datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)
    return when.strftime("%Y-%m-%d %H:%M UTC")


def _snapshot_fields(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    load = snapshot.get("loadavg") or {}
    mem = snapshot.get("memory") or {}
    disk = snapshot.get("disk") or {}
    failed = snapshot.get("failed_units") or {}
    patches = snapshot.get("zypper_patch_check") or {}
    mem_pct = mem.get("used_pct")
    disk_pct = disk.get("used_pct")
    failed_out = failed.get("stdout") or failed.get("stderr") or f"exit {failed.get('exit')}"
    patch_out = patches.get("stdout") or patches.get("stderr") or f"exit {patches.get('exit')}"
    return [
        _field("host", str(snapshot.get("hostname") or "-")),
        _field("load", f"{load.get('1')} / {load.get('5')} / {load.get('15')}"),
        _field("mem used %", "-" if mem_pct is None else str(mem_pct)),
        _field("disk / %", "-" if disk_pct is None else str(disk_pct)),
        _field("failed units", str(failed_out), inline=False),
        _field("zypper patch-check", str(patch_out), inline=False),
    ]


class DiscordClient:
    def __init__(self, config: Config) -> None:
        self.config = config

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.discord_webhook_url:
            return {"ok": False, "error": "DISCORD_WEBHOOK_URL missing"}
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(self.config.discord_webhook_url, json=payload)
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}
        if response.status_code >= 400:
            return {"ok": False, "error": f"HTTP {response.status_code}: {response.text[:400]}"}
        return {"ok": True, "status": response.status_code}

    def post_report(
        self,
        snapshot: dict[str, Any],
        report: dict[str, Any] | None,
        extra_actions: list[str] | None = None,
        harness_note: str | None = None,
    ) -> dict[str, Any]:
        severity = (report or {}).get("severity") or "warn"
        if severity not in COLORS:
            severity = "warn"
        headline = (report or {}).get("headline") or "Window complete (no LLM report)"
        findings = (report or {}).get("findings") or []
        actions = list((report or {}).get("actions") or [])
        if extra_actions:
            actions.extend(extra_actions)
        next_focus = (report or {}).get("next_focus") or "-"
        description_parts = [headline]
        if harness_note:
            description_parts.append(harness_note)
        content = None
        if severity == "crit" and self.config.discord_crit_role_id:
            content = f"<@&{self.config.discord_crit_role_id}>"
        embed = {
            "title": f"[{SEVERITY_EMOJI.get(severity, severity)}] {snapshot.get('hostname', 'host')}",
            "description": _clip("\n".join(description_parts), 3900),
            "color": COLORS[severity],
            "fields": [
                *_snapshot_fields(snapshot),
                _field("findings", "\n".join(f"- {item}" for item in findings) or "-", inline=False),
                _field("actions", "\n".join(f"- {item}" for item in actions) or "-", inline=False),
                _field("next focus", str(next_focus), inline=False),
                _field("next check", _next_check(self.config.interval_seconds)),
                _field("dry_run", str(self.config.dry_run)),
                _field("interval s", str(self.config.interval_seconds)),
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload: dict[str, Any] = {"embeds": [embed]}
        if content:
            payload["content"] = content
        result = self._post(payload)
        result["posted_at"] = datetime.now(timezone.utc).isoformat()
        return result

    def post_error(self, message: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot = snapshot or {}
        embed = {
            "title": f"[ERROR] {snapshot.get('hostname', 'harness')}",
            "description": _clip(message, 3900),
            "color": COLORS["error"],
            "fields": _snapshot_fields(snapshot) if snapshot else [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        extra = []
        extra.append(_field("next check", _next_check(self.config.interval_seconds)))
        embed["fields"] = (embed.get("fields") or []) + extra
        return self._post({"embeds": [embed]})

    def post_crit_alert(self, headline: str, detail: str) -> dict[str, Any]:
        content = None
        if self.config.discord_crit_role_id:
            content = f"<@&{self.config.discord_crit_role_id}>"
        embed = {
            "title": f"[CRIT ALERT] {headline}",
            "description": _clip(detail, 3900),
            "color": COLORS["crit"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload: dict[str, Any] = {"embeds": [embed]}
        if content:
            payload["content"] = content
        return self._post(payload)
