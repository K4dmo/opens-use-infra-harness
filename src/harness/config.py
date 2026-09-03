from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass(frozen=True)
class Config:
    openrouter_api_key: str
    openrouter_model: str
    openrouter_fallback_model: str
    openrouter_referer: str
    openrouter_title: str
    discord_webhook_url: str
    discord_crit_role_id: str
    interval_seconds: int
    dry_run: bool
    allow_patches: bool
    use_sudo: bool
    max_tool_rounds: int
    max_actions_per_window: int
    circuit_failure_threshold: int
    circuit_reset: bool
    allowed_units: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    data_dir: Path
    prompt_path: Path
    command_timeout_seconds: int
    repo_root: Path = field(repr=False)

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @classmethod
    def from_env(cls, repo_root: Path | None = None) -> Config:
        root = repo_root or Path(__file__).resolve().parents[2]
        explicit = os.environ.get("INFRA_HARNESS_ENV")
        candidates = []
        if explicit:
            candidates.append(Path(explicit))
        candidates.extend(
            [
                Path("/etc/infra-harness.env"),
                root / "config.env",
                root / ".env",
            ]
        )
        for path in candidates:
            load_env_file(path)

        prompt_override = os.environ.get("PROMPT_PATH", "").strip()
        prompt_path = Path(prompt_override) if prompt_override else root / "prompts" / "sre.md"

        data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
        units = _csv(os.environ.get("ALLOWED_UNITS", "sshd.service,cron.service"))
        paths = _csv(
            os.environ.get(
                "ALLOWED_PATHS",
                "/etc/infra-harness,/opt/infra-harness,/var/lib/infra-harness",
            )
        )

        return cls(
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
            openrouter_model=os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4").strip(),
            openrouter_fallback_model=os.environ.get(
                "OPENROUTER_FALLBACK_MODEL", "openai/gpt-4o-mini"
            ).strip(),
            openrouter_referer=os.environ.get("OPENROUTER_REFERER", "https://localhost").strip(),
            openrouter_title=os.environ.get("OPENROUTER_TITLE", "opensuse-infra-harness").strip(),
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", "").strip(),
            discord_crit_role_id=os.environ.get("DISCORD_CRIT_ROLE_ID", "").strip(),
            interval_seconds=int(os.environ.get("INTERVAL_SECONDS", "900")),
            dry_run=_truthy(os.environ.get("DRY_RUN"), default=True),
            allow_patches=_truthy(os.environ.get("ALLOW_PATCHES"), default=False),
            use_sudo=_truthy(os.environ.get("USE_SUDO"), default=True),
            max_tool_rounds=int(os.environ.get("MAX_TOOL_ROUNDS", "8")),
            max_actions_per_window=int(os.environ.get("MAX_ACTIONS_PER_WINDOW", "5")),
            circuit_failure_threshold=int(os.environ.get("CIRCUIT_FAILURE_THRESHOLD", "3")),
            circuit_reset=_truthy(os.environ.get("CIRCUIT_RESET"), default=False),
            allowed_units=tuple(units),
            allowed_paths=tuple(paths),
            data_dir=data_dir,
            prompt_path=prompt_path,
            command_timeout_seconds=int(os.environ.get("COMMAND_TIMEOUT_SECONDS", "60")),
            repo_root=root,
        )

    def require_webhook(self) -> None:
        if not self.discord_webhook_url:
            raise SystemExit("DISCORD_WEBHOOK_URL is required")
