from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from harness.audit import append_audit
from harness.config import Config
from harness.discord import DiscordClient
from harness.openrouter import OpenRouterClient, OpenRouterError, message_from_choice
from harness.policy import PolicyExecutor
from harness.snapshot import collect_snapshot
from harness.state import load_state, memory_for_prompt, save_state
from harness.tools import TOOL_SCHEMAS, ToolDispatcher, parse_tool_args


def _load_prompt(config: Config) -> str:
    try:
        return config.prompt_path.read_text(encoding="utf-8")
    except OSError:
        return "You are the SRE for this OpenSUSE host. Inspect with tools, then call submit_report."


def _tool_call_payload(tool_call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    fn = tool_call.get("function") or {}
    name = str(fn.get("name") or "")
    args = parse_tool_args(fn.get("arguments"))
    call_id = str(tool_call.get("id") or name)
    return call_id, name, args


def run_window(config: Config) -> dict[str, Any]:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.backups_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(config.state_path)
    executor = PolicyExecutor(config)
    if config.circuit_reset:
        state["consecutive_failures"] = 0
        state["circuit_open"] = False
    executor.apply_circuit_from_state(
        int(state.get("consecutive_failures") or 0),
        reset=config.circuit_reset,
    )

    snapshot = collect_snapshot(timeout=config.command_timeout_seconds)
    discord = DiscordClient(config)
    window_error: str | None = None
    report: dict[str, Any] | None = None
    used_llm = False

    def notify(headline: str, detail: str) -> dict[str, Any]:
        result = discord.post_crit_alert(headline, detail)
        append_audit(config.audit_path, {"tool": "notify_discord", **result, "headline": headline})
        return result

    dispatcher = ToolDispatcher(executor, notify_discord=notify)

    if config.openrouter_api_key:
        try:
            report = _run_agent(config, snapshot, state, dispatcher)
            used_llm = True
        except OpenRouterError as exc:
            window_error = f"OpenRouter failed: {exc}"
        except Exception as exc:  # noqa: BLE001 — window must always Discord
            window_error = f"agent failed: {exc}"
    else:
        window_error = "OPENROUTER_API_KEY missing; posting baseline snapshot only"

    if dispatcher.report:
        report = dispatcher.report

    action_summaries = [
        f"{a.get('name')}: {'denied' if a.get('denied') else 'dry_run' if a.get('dry_run') else 'exit=' + str(a.get('exit', 'n/a'))}"
        for a in executor.window_actions
        if a.get("mutating") or a.get("denied")
    ]

    llm_down = not used_llm or window_error
    mutation_failed = executor.action_failures > 0
    if llm_down or mutation_failed:
        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
    else:
        state["consecutive_failures"] = 0
    state["circuit_open"] = state["consecutive_failures"] >= config.circuit_failure_threshold

    if window_error and not report:
        posted = discord.post_error(window_error, snapshot)
    else:
        posted = discord.post_report(
            snapshot,
            report,
            extra_actions=action_summaries,
            harness_note=window_error,
        )

    state["last_snapshot"] = {
        "collected_at": snapshot.get("collected_at"),
        "hostname": snapshot.get("hostname"),
        "loadavg": snapshot.get("loadavg"),
        "memory": snapshot.get("memory"),
        "disk": snapshot.get("disk"),
        "failed_units_exit": (snapshot.get("failed_units") or {}).get("exit"),
        "failed_units": (snapshot.get("failed_units") or {}).get("stdout"),
        "zypper_exit": (snapshot.get("zypper_patch_check") or {}).get("exit"),
    }
    state["last_report"] = report or {"error": window_error}
    state["last_actions"] = executor.window_actions
    state["findings"] = (state.get("findings") or [])[-50:] + executor.window_findings
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    state["last_discord_at"] = posted.get("posted_at")
    save_state(config.state_path, state)
    append_audit(
        config.audit_path,
        {
            "event": "window",
            "error": window_error,
            "discord_ok": posted.get("ok"),
            "circuit_open": state["circuit_open"],
            "consecutive_failures": state["consecutive_failures"],
            "dry_run": config.dry_run,
        },
    )
    return {
        "ok": posted.get("ok") and not window_error,
        "error": window_error,
        "report": report,
        "discord": posted,
        "circuit_open": state["circuit_open"],
    }


def _run_agent(
    config: Config,
    snapshot: dict[str, Any],
    state: dict[str, Any],
    dispatcher: ToolDispatcher,
) -> dict[str, Any] | None:
    client = OpenRouterClient(config)
    user_payload = {
        "snapshot": snapshot,
        "memory": memory_for_prompt(state),
        "policy": {
            "dry_run": config.dry_run,
            "allow_patches": config.allow_patches,
            "allowed_units": list(config.allowed_units),
            "allowed_paths": list(config.allowed_paths),
            "circuit_open": dispatcher.executor.circuit_open,
            "max_tool_rounds": config.max_tool_rounds,
            "max_actions_per_window": config.max_actions_per_window,
        },
        "instruction": "Inspect if needed, act only within tools/policy, then call submit_report.",
    }
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _load_prompt(config)},
        {"role": "user", "content": json.dumps(user_payload, default=str)[:120000]},
    ]

    for _round in range(config.max_tool_rounds):
        payload = client.chat_with_fallback(messages, tools=TOOL_SCHEMAS)
        message = message_from_choice(payload)
        messages.append(message)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            content = message.get("content") or ""
            parsed = _parse_json_report(content)
            if parsed:
                dispatcher.report = parsed
            break
        for tool_call in tool_calls:
            call_id, name, args = _tool_call_payload(tool_call)
            result = dispatcher.dispatch(name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(result, default=str)[:20000],
                }
            )
            if name == "submit_report":
                return dispatcher.report
    return dispatcher.report


def _parse_json_report(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if "```" in text:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if "severity" in data and "headline" in data:
        return data
    return None


def run_forever(config: Config) -> None:
    while True:
        run_window(config)
        time.sleep(max(30, config.interval_seconds))
