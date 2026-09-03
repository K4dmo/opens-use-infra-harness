# OpenSUSE infra harness (POC)

Host-local SRE loop for a single OpenSUSE server: snapshot, OpenRouter tool-calling, bounded actions, Discord webhook. Python 3.9+, `httpx`, systemd timer (or in-process loop). Not part of Yungu. No UI; static env vars.

## What it does

1. Snapshot (no LLM): hostname, load, memory, disk, failed systemd units, `zypper patch-check`, recent journal errors.
2. Agent turn: OpenRouter tool-calling (`MAX_TOOL_ROUNDS`). The model decides what to inspect.
3. Policy executor: argv prefix allowlists, path/unit allowlists, `DRY_RUN`, action cap, circuit breaker, backups before writes, `audit.jsonl`.
4. Discord embed: severity, metrics, findings, actions, next check time. If OpenRouter is down, the snapshot is still posted.

Default deploy is **`DRY_RUN=true`**. Flip to `false` only after you have watched a few Discord windows.

## Layout

- [`src/harness/`](src/harness/) — loop, OpenRouter client, tools, policy, Discord, state
- [`prompts/sre.md`](prompts/sre.md) — system prompt
- [`config.example.env`](config.example.env) — static variables
- [`deploy/`](deploy/) — systemd unit/timer, sudoers, install script

## Config

Copy [`config.example.env`](config.example.env) to `/etc/infra-harness.env` (mode `0600`) or `config.env` in the repo for local runs.

Required:

- `OPENROUTER_API_KEY`
- `DISCORD_WEBHOOK_URL`

Important:

- `INTERVAL_SECONDS` — used by the in-process loop and the Discord “next check” field. Keep it aligned with `OnUnitActiveSec` in [`deploy/infra-harness.timer`](deploy/infra-harness.timer) (default 15 minutes / 900s).
- `DRY_RUN=true` until you trust the reports
- `ALLOW_PATCHES=false` until you want unattended `zypper patch`
- `ALLOWED_UNITS` / `ALLOWED_PATHS` — keep in sync with [`deploy/sudoers`](deploy/sudoers)
- `CIRCUIT_FAILURE_THRESHOLD` — after this many failed windows (LLM down or mutating command non-zero), mutations stop until `CIRCUIT_RESET=true` or you clear `state.json`
- `DATA_DIR` — `state.json`, `audit.jsonl`, `backups/`

## Local run (dev machine)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp config.example.env config.env
# fill keys; set DATA_DIR to ./data and USE_SUDO=false for a laptop smoke test
PYTHONPATH=src .venv/bin/python -m harness --once --env-file config.env
```

On macOS, `systemctl` / `zypper` / `journalctl` will show as missing in the snapshot; Discord should still receive the embed.

```bash
PYTHONPATH=src .venv/bin/pytest
```

## OpenSUSE install

As root, from the clone:

```bash
bash deploy/install.sh
# edit /etc/infra-harness.env
systemctl enable --now infra-harness.timer
```

One-shot test:

```bash
systemctl start infra-harness.service
journalctl -u infra-harness.service -n 50 --no-pager
```

Prefer the **timer + oneshot** unit so a hung window cannot block the next tick. [`deploy/infra-harness-loop.service`](deploy/infra-harness-loop.service) is the alternative in-process sleep loop (`Restart=always`).

Dedicated user: `infra-agent`. Sudo is command-limited, not `ALL`.

## Tools the model may call

`get_snapshot`, `run_inspect`, `service_ctl`, `read_file`, `write_file`, `zypper`, `notify_discord`, `record_finding`, `submit_report`.

`run_inspect` is prefix-allowlisted (no shell). Destructive tokens (`rm`, `mkfs`, `dd`, firewall tools, `curl`/`wget`, …) are denied.

## Non-goals

Network pentests, exploits, scanning other hosts, custom trained weights, Yungu integration.
