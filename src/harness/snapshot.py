from __future__ import annotations

import os
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from typing import Any


def _run(argv: list[str], timeout: int = 20) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = (proc.stdout or "")[-4000:]
        stderr = (proc.stderr or "")[-1500:]
        return {
            "argv": argv,
            "exit": proc.returncode,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        }
    except FileNotFoundError:
        return {"argv": argv, "exit": 127, "stdout": "", "stderr": "not found"}
    except subprocess.TimeoutExpired:
        return {"argv": argv, "exit": 124, "stdout": "", "stderr": "timeout"}


def _meminfo() -> dict[str, Any]:
    path = "/proc/meminfo"
    if os.path.isfile(path):
        info: dict[str, int] = {}
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                try:
                    info[key] = int(parts[1])
                except ValueError:
                    continue
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        used = total - available if total else 0
        pct = round(100.0 * used / total, 1) if total else None
        return {"source": "proc", "kb_total": total, "kb_available": available, "used_pct": pct}
    try:
        proc = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=10)
        return {"source": "free", "text": (proc.stdout or "")[:800]}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"source": "none"}


def _disk() -> dict[str, Any]:
    usage = shutil.disk_usage("/")
    pct = round(100.0 * usage.used / usage.total, 1) if usage.total else None
    return {
        "path": "/",
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_pct": pct,
    }


def collect_snapshot(timeout: int = 20) -> dict[str, Any]:
    load1 = load5 = load15 = None
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        pass

    failed = _run(["systemctl", "--failed", "--no-legend", "--no-pager"], timeout=timeout)
    patches = _run(["zypper", "--non-interactive", "patch-check"], timeout=timeout)
    journal = _run(
        ["journalctl", "-p", "err", "-n", "20", "--no-pager", "-o", "short-iso"],
        timeout=timeout,
    )

    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "uname": _run(["uname", "-a"], timeout=timeout),
        "uptime": _run(["uptime"], timeout=timeout),
        "loadavg": {"1": load1, "5": load5, "15": load15},
        "memory": _meminfo(),
        "disk": _disk(),
        "failed_units": failed,
        "zypper_patch_check": patches,
        "journal_err": journal,
    }
