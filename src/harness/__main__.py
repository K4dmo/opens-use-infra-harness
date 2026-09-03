from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.config import Config
from harness.loop import run_forever, run_window


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenSUSE infra harness (POC)")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single window and exit (use with systemd timer)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional env file (also: INFRA_HARNESS_ENV, /etc/infra-harness.env)",
    )
    args = parser.parse_args(argv)

    if args.env_file:
        from harness.config import load_env_file

        load_env_file(args.env_file)

    config = Config.from_env()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.require_webhook()

    if args.once:
        result = run_window(config)
        if result.get("ok"):
            return 0
        print(result.get("error") or "window completed with Discord/LLM issues", file=sys.stderr)
        # Still 0 if Discord posted a failure embed: operators were notified.
        return 0 if (result.get("discord") or {}).get("ok") else 1

    run_forever(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
