"""Validate slash input before any native operation can be dispatched."""

import json
import re
from typing import Any

from connector.runtimes.codex.domain.commands import ALIASES

GOAL_STATUSES = {
    "active",
    "paused",
    "blocked",
    "usageLimited",
    "budgetLimited",
    "complete",
}


def command_input(
    command: str, raw: str | None, args: tuple[str, ...]
) -> tuple[str, str]:
    if not isinstance(command, str) or not re.fullmatch(
        r"/?[A-Za-z][A-Za-z0-9_-]*", command
    ):
        raise ValueError("Invalid command name")
    name = command.removeprefix("/").lower()
    name = ALIASES.get(name, name)
    if not isinstance(args, (tuple, list)) or any(
        not isinstance(arg, str) for arg in args
    ):
        raise ValueError("Command arguments must be strings")
    if raw is not None:
        if not isinstance(raw, str) or len(raw) > 4096:
            raise ValueError("Command raw input must contain at most 4096 characters")
        match = re.fullmatch(r"\s*/([A-Za-z][A-Za-z0-9_-]*)(?:\s+([\s\S]*))?", raw)
        if not match or ALIASES.get(match[1].lower(), match[1].lower()) != name:
            raise ValueError("Raw command must match the requested command")
        text = match[2] or ""
    else:
        if len(args) > 1:
            raise ValueError("Provide one free-form argument or exact raw input")
        text = args[0] if args else ""
    return name, text


def parse_goal(text: str) -> tuple[str, dict[str, Any]]:
    action, *rest = re.split(r"\s+", text.strip(), maxsplit=1)
    tail = rest[0] if rest else ""
    action = action or "status"
    if action in {"status", "pause", "resume", "clear"}:
        if tail:
            raise ValueError("This goal action takes no arguments")
        return action, (
            {"status": "paused" if action == "pause" else "active"}
            if action in {"pause", "resume"}
            else {}
        )
    if action == "budget":
        payload = {"tokenBudget": json.loads(tail)}
    else:
        if action not in {"create", "edit", "set"}:
            action, tail = "create", text.strip()
        payload = (
            json.loads(tail)
            if tail.startswith("{") or action == "set"
            else {"objective": tail}
        )
        if (
            not isinstance(payload, dict)
            or not payload
            or set(payload) - {"objective", "status", "tokenBudget"}
        ):
            raise ValueError(
                "Goal payload requires objective, status or tokenBudget only"
            )
        if action in {"create", "edit"} and "objective" not in payload:
            raise ValueError("An objective is required")
        if action == "create":
            payload.setdefault("status", "active")
    if "objective" in payload and (
        not isinstance(payload["objective"], str) or not payload["objective"].strip()
    ):
        raise ValueError("Objective must be nonempty text")
    if "status" in payload and (
        not isinstance(payload["status"], str) or payload["status"] not in GOAL_STATUSES
    ):
        raise ValueError("Invalid goal status")
    budget = payload.get("tokenBudget")
    if budget is not None and (type(budget) is not int or not 0 < budget <= 2**63 - 1):
        raise ValueError("Token budget must be a positive integer or null")
    return action, payload


def parse_payload(name: str, text: str) -> tuple[str, dict[str, Any]] | dict[str, str] | str:
    if name == "goal":
        return parse_goal(text)
    if name == "review":
        parts = re.split(r"\s+", text.strip(), maxsplit=1)
        target, argument = parts[0], parts[1] if len(parts) == 2 else ""
        if target in {"", "uncommitted"} and not argument:
            return {"type": "uncommittedChanges"}
        if target in {"branch", "commit", "custom"} and argument.strip():
            return {
                "type": {
                    "branch": "baseBranch",
                    "commit": "commit",
                    "custom": "custom",
                }[target],
                {"branch": "branch", "commit": "sha", "custom": "instructions"}[
                    target
                ]: argument,
            }
        raise ValueError("Review requires uncommitted, branch, commit or custom target")
    if name == "plan":
        if text.strip() not in {"", "on", "off"} or "\n" in text:
            raise ValueError("Plan accepts on or off")
        return "default" if text.strip() == "off" else "plan"
    if text.strip():
        raise ValueError("This command takes no arguments")
    return {}
