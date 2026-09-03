from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.audit import append_audit
from harness.config import Config

DENY_PATH_NAMES = {
    "shadow",
    "shadow-",
    "passwd",
    "passwd-",
    "sudoers",
    "gshadow",
    "gshadow-",
}

DENY_PATH_PREFIXES = (
    "/etc/sudoers.d",
    "/etc/shadow",
    "/root/.ssh",
    "/home/",
)

DENY_TOKENS = (
    "mkfs",
    "dd",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "userdel",
    "passwd",
    "visudo",
    "iptables",
    "nft",
    "firewall-cmd",
    "curl",
    "wget",
    "chmod",
    "chown",
    "rm",
)

SHELL_META = re.compile(r"[|&;`$<>\\]")

INSPECT_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("systemctl", "status"),
    ("systemctl", "is-active"),
    ("systemctl", "is-failed"),
    ("systemctl", "--failed"),
    ("systemctl", "list-units"),
    ("systemctl", "show"),
    ("journalctl",),
    ("df",),
    ("free",),
    ("uptime",),
    ("ss",),
    ("zypper", "lu"),
    ("zypper", "patch-check"),
    ("zypper", "list-patches"),
    ("zypper", "--non-interactive", "patch-check"),
    ("zypper", "--non-interactive", "list-patches"),
    ("zypper", "--non-interactive", "lu"),
    ("rpm", "-q"),
    ("rpm", "-qa"),
    ("uname",),
    ("hostnamectl",),
    ("ip", "addr"),
    ("ip", "-br", "addr"),
    ("who",),
    ("last",),
)

JOURNALCTL_FLAGS = {
    "-n",
    "-u",
    "--unit",
    "--since",
    "--no-pager",
    "-p",
    "--priority",
    "-b",
    "--boot",
    "-o",
    "--output",
    "-q",
    "--quiet",
    "--disk-usage",
}

SS_FLAGS = {"-l", "-n", "-t", "-u", "-p", "-lntup", "-tulpn", "-ltn", "-lun", "-lntu", "-H"}


def _truncate(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...[truncated]..."


def argv_matches_prefix(argv: list[str], prefix: tuple[str, ...]) -> bool:
    if len(argv) < len(prefix):
        return False
    return tuple(argv[: len(prefix)]) == prefix


def inspect_allowed(argv: list[str]) -> str | None:
    if not argv:
        return "empty argv"
    if any(SHELL_META.search(part) for part in argv):
        return "shell metacharacters are not allowed"
    lowered = [a.lower() for a in argv]
    if lowered[0] in DENY_TOKENS or (len(lowered) > 1 and lowered[0] == "sudo" and lowered[1] in DENY_TOKENS):
        return f"command {argv[0]} is denied"
    if not any(argv_matches_prefix(argv, prefix) for prefix in INSPECT_PREFIXES):
        return f"command not on inspect allowlist: {argv}"
    if argv[0] == "journalctl":
        for arg in argv[1:]:
            if arg.startswith("-"):
                flag = arg.split("=", 1)[0]
                if flag not in JOURNALCTL_FLAGS:
                    return f"journalctl flag not allowed: {arg}"
    if argv[0] == "ss":
        for arg in argv[1:]:
            if arg.startswith("-") and arg.split("=", 1)[0] not in SS_FLAGS:
                return f"ss flag not allowed: {arg}"
    return None


def path_allowed(path: Path, allowed_prefixes: tuple[str, ...]) -> str | None:
    resolved = Path(os_path_norm(str(path))).resolve(strict=False)
    text = str(resolved)
    name = resolved.name
    if name in DENY_PATH_NAMES:
        return f"path name denied: {name}"
    for prefix in DENY_PATH_PREFIXES:
        if prefix == "/home/":
            if text.startswith("/home/"):
                return "home directories are denied"
            continue
        if text == prefix.rstrip("/") or text.startswith(prefix):
            return f"path prefix denied: {prefix}"
    if not allowed_prefixes:
        return "no ALLOWED_PATHS configured"
    for prefix in allowed_prefixes:
        pref = str(Path(prefix).resolve(strict=False))
        if text == pref or text.startswith(pref.rstrip("/") + "/"):
            return None
    return f"path not under ALLOWED_PATHS: {text}"


def os_path_norm(text: str) -> str:
    return str(Path(text).expanduser())


def unit_allowed(unit: str, allowed: tuple[str, ...]) -> str | None:
    if unit not in allowed:
        return f"unit not on ALLOWED_UNITS: {unit}"
    if not re.fullmatch(r"[A-Za-z0-9:_.\\-]+", unit):
        return "invalid unit name"
    return None


class PolicyExecutor:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.actions_used = 0
        self.action_failures = 0
        self.circuit_open = False
        self.window_actions: list[dict[str, Any]] = []
        self.window_findings: list[dict[str, Any]] = []

    def apply_circuit_from_state(self, consecutive_failures: int, reset: bool) -> None:
        if reset:
            self.circuit_open = False
            return
        self.circuit_open = consecutive_failures >= self.config.circuit_failure_threshold

    def _audit(self, **event: Any) -> None:
        append_audit(self.config.audit_path, event)

    def _maybe_sudo(self, argv: list[str]) -> list[str]:
        if not self.config.use_sudo:
            return argv
        if argv and argv[0] == "sudo":
            return argv
        return ["sudo", "-n", *argv]

    def _run(self, argv: list[str]) -> dict[str, Any]:
        import subprocess

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
                check=False,
            )
            result = {
                "argv": argv,
                "exit": proc.returncode,
                "stdout": _truncate((proc.stdout or "").strip()),
                "stderr": _truncate((proc.stderr or "").strip(), 2000),
            }
        except FileNotFoundError:
            result = {"argv": argv, "exit": 127, "stdout": "", "stderr": "not found"}
        except subprocess.TimeoutExpired:
            result = {"argv": argv, "exit": 124, "stdout": "", "stderr": "timeout"}
        self._audit(tool="run", **result)
        return result

    def _mutation_blocked(self) -> dict[str, Any] | None:
        if self.circuit_open:
            return {"ok": False, "denied": True, "reason": "circuit_open"}
        if self.actions_used >= self.config.max_actions_per_window:
            return {
                "ok": False,
                "denied": True,
                "reason": "max_actions_per_window",
                "limit": self.config.max_actions_per_window,
            }
        return None

    def _note_action(self, name: str, mutating: bool, result: dict[str, Any]) -> None:
        entry = {"name": name, "mutating": mutating, **{k: result.get(k) for k in ("ok", "denied", "dry_run", "exit", "reason")}}
        self.window_actions.append(entry)
        if mutating and not result.get("denied") and not result.get("dry_run"):
            self.actions_used += 1
            if result.get("exit") not in (0, None) or result.get("ok") is False:
                self.action_failures += 1

    def run_inspect(self, argv: list[str]) -> dict[str, Any]:
        reason = inspect_allowed(argv)
        if reason:
            result = {"ok": False, "denied": True, "reason": reason, "argv": argv}
            self._audit(tool="run_inspect", **result)
            self._note_action("run_inspect", False, result)
            return result
        ran = self._run(argv)
        result = {"ok": ran["exit"] == 0, **ran}
        self._note_action("run_inspect", False, result)
        return result

    def service_ctl(self, unit: str, action: str) -> dict[str, Any]:
        action = action.strip().lower()
        if action not in {"status", "restart", "start", "stop", "is-failed", "is-active"}:
            result = {"ok": False, "denied": True, "reason": f"unsupported action: {action}"}
            self._note_action("service_ctl", False, result)
            return result
        unit_reason = unit_allowed(unit, self.config.allowed_units)
        if unit_reason:
            result = {"ok": False, "denied": True, "reason": unit_reason}
            self._note_action("service_ctl", False, result)
            return result
        mutating = action in {"restart", "start", "stop"}
        if mutating:
            blocked = self._mutation_blocked()
            if blocked:
                self._note_action("service_ctl", True, blocked)
                return blocked
            if self.config.dry_run:
                result = {
                    "ok": True,
                    "dry_run": True,
                    "would_run": ["systemctl", action, unit],
                }
                self._note_action("service_ctl", True, result)
                self._audit(tool="service_ctl", **result)
                return result
        argv = self._maybe_sudo(["systemctl", action, unit, "--no-pager"])
        ran = self._run(argv)
        result = {"ok": ran["exit"] == 0, **ran}
        self._note_action("service_ctl", mutating, result)
        return result

    def read_file(self, path_str: str) -> dict[str, Any]:
        path = Path(path_str)
        reason = path_allowed(path, self.config.allowed_paths)
        if reason:
            result = {"ok": False, "denied": True, "reason": reason}
            self._note_action("read_file", False, result)
            return result
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result = {"ok": False, "error": str(exc), "path": str(path)}
            self._note_action("read_file", False, result)
            return result
        result = {"ok": True, "path": str(path), "content": _truncate(text, 8000)}
        self._note_action("read_file", False, result)
        self._audit(tool="read_file", path=str(path), ok=True, bytes=len(text))
        return result

    def write_file(self, path_str: str, content: str) -> dict[str, Any]:
        path = Path(path_str)
        reason = path_allowed(path, self.config.allowed_paths)
        if reason:
            result = {"ok": False, "denied": True, "reason": reason}
            self._note_action("write_file", True, result)
            return result
        blocked = self._mutation_blocked()
        if blocked:
            self._note_action("write_file", True, blocked)
            return blocked
        if self.config.dry_run:
            result = {
                "ok": True,
                "dry_run": True,
                "path": str(path),
                "bytes": len(content.encode("utf-8")),
            }
            self._note_action("write_file", True, result)
            self._audit(tool="write_file", **result)
            return result
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_root = self.config.backups_dir / stamp
        try:
            if path.exists() and path.is_file():
                rel = path.resolve().as_posix().lstrip("/")
                dest = backup_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
                backup = str(dest)
            else:
                backup = None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            result = {"ok": False, "error": str(exc), "path": str(path)}
            self._note_action("write_file", True, result)
            return result
        result = {"ok": True, "path": str(path), "backup": backup}
        self._note_action("write_file", True, result)
        self._audit(tool="write_file", **result)
        return result

    def zypper(self, action: str) -> dict[str, Any]:
        action = action.strip().lower().replace("_", "-")
        if action in {"patch-check", "list-patches", "lu"}:
            argv = self._maybe_sudo(["zypper", "--non-interactive", action])
            ran = self._run(argv)
            result = {"ok": ran["exit"] in {0, 100, 101} or ran["exit"] == 0, **ran}
            # zypper patch-check uses 100/101 for patches available
            if ran["exit"] in {100, 101}:
                result["ok"] = True
                result["patches_pending"] = True
            self._note_action("zypper", False, result)
            return result
        if action != "patch":
            result = {"ok": False, "denied": True, "reason": f"unsupported zypper action: {action}"}
            self._note_action("zypper", False, result)
            return result
        if not self.config.allow_patches:
            result = {"ok": False, "denied": True, "reason": "ALLOW_PATCHES is false"}
            self._note_action("zypper", True, result)
            return result
        blocked = self._mutation_blocked()
        if blocked:
            self._note_action("zypper", True, blocked)
            return blocked
        argv = ["zypper", "--non-interactive", "patch"]
        if self.config.dry_run:
            result = {"ok": True, "dry_run": True, "would_run": argv}
            self._note_action("zypper", True, result)
            self._audit(tool="zypper", **result)
            return result
        ran = self._run(self._maybe_sudo(argv))
        result = {"ok": ran["exit"] == 0, **ran}
        self._note_action("zypper", True, result)
        return result

    def record_finding(self, severity: str, title: str, detail: str) -> dict[str, Any]:
        finding = {
            "severity": severity,
            "title": title,
            "detail": _truncate(detail, 2000),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.window_findings.append(finding)
        self._audit(tool="record_finding", **finding)
        return {"ok": True, "stored": True}
