"""Commands supported by AA's native Codex runtime and selection controls."""

from connector.runtime_protocol import RuntimeCommand

ONLINE = ("idle", "error", "running", "waiting", "waiting_approval", "blocked")
IDLE = ("idle", "error")
ALIASES = {
    "compact-thread": "compact",
    "plan-mode": "plan",
    "permissions": "permission",
}
HINTS = {
    "goal": "<objective> | status | create <objective or JSON> | edit <objective or JSON> | pause | resume | clear | budget <integer|null> | set <JSON>",
    "review": "uncommitted | branch <name> | commit <sha> | custom <instructions>",
    "plan": "on | off",
}
DESCRIPTIONS = {
    "status": "Show the current session status.",
    "compact": "Compact the session context.",
    "goal": "View or manage the native session goal.",
    "review": "Start a code review in this session.",
    "plan": "Set planning mode for subsequent turns.",
    "model": "Choose a model.",
    "reasoning": "Choose reasoning effort.",
    "permission": "Choose a permission mode.",
}


def list_codex_commands(
    external_session_id: str | None,
    client_available: bool,
    query: str | None = None,
    limit: int = 50,
    *,
    status: str = "idle",
    source_availability: str | None = None,
    native_commands: bool = False,
    plan_available: bool = False,
) -> tuple[RuntimeCommand, ...]:
    commands = []
    for name, description in DESCRIPTIONS.items():
        statuses = IDLE if name in {"compact", "review", "plan"} else ONLINE
        if name == "status":
            statuses = (*ONLINE, "unknown")
        reason = None
        if not client_available:
            reason = "codex_unavailable"
        elif not external_session_id:
            reason = "session_unloaded"
        elif name != "status" and source_availability in {
            "archived",
            "unavailable",
            "deleted",
            "missing",
        }:
            reason = f"session_{source_availability}"
        elif (
            name in {"goal", "review"}
            and not native_commands
            or name == "plan"
            and not plan_available
        ):
            reason = "native_commands_unavailable"
        elif status not in statuses:
            reason = f"session_{status}"
        ui = {
            "kind": "execute",
            "allowedStatuses": list(statuses),
            "acceptsMultiline": name in {"goal", "review"},
        }
        if name in HINTS:
            ui["argumentHint"] = HINTS[name]
        if name in {"model", "reasoning", "permission"}:
            ui = {"kind": "selector", "target": name}
        commands.append(
            RuntimeCommand(
                id=name,
                title=name.capitalize(),
                description=description,
                aliases=tuple(
                    alias for alias, target in ALIASES.items() if target == name
                ),
                enabled=reason is None,
                disabled_reason=reason,
                accepts_args=name in HINTS,
                args_schema={"type": "string"} if name in HINTS else None,
                metadata={"ui": ui},
            )
        )
    needle = (query or "").strip().casefold()
    return tuple(
        c
        for c in commands
        if not needle
        or needle
        in " ".join((c.id, c.title, c.description or "", *c.aliases)).casefold()
    )[: max(0, min(limit, 1000))]
