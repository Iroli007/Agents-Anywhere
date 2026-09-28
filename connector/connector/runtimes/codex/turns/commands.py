"""Execute native commands once, keeping errors and ambiguous outcomes explicit."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakValueDictionary

from openai_codex.errors import (
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
)
from openai_codex.generated.v2_all import ThreadGoal

from connector.runtime_protocol import (
    RuntimeCommand,
    RuntimeCommandResult,
    RuntimeConflictError,
    RuntimeInvalidRequestError,
    RuntimeSessionSourceStateCache,
    RuntimeSessionStateCache,
)
from connector.runtimes.codex.domain.commands import list_codex_commands
from connector.runtimes.codex.sdk.runtime_client import CodexRuntimeClient
from connector.runtimes.codex.turns.command_input import command_input, parse_payload


class CommandUnavailable(ValueError):
    """A command's native precondition failed before mutation."""


@dataclass(slots=True)
class CodexCommandController:
    client: CodexRuntimeClient | None
    states: RuntimeSessionStateCache
    source_states: RuntimeSessionSourceStateCache
    ensure_started: Callable[[], Awaitable[None]]
    _goal_locks: WeakValueDictionary[str, asyncio.Lock] = field(
        default_factory=WeakValueDictionary, init=False
    )

    def catalog(
        self,
        session_id: str,
        thread_id: str | None,
        query: str | None = None,
        limit: int = 50,
    ) -> tuple[RuntimeCommand, ...]:
        state = self.states.get(session_id)
        source = self.source_states.get(session_id)
        return list_codex_commands(
            thread_id,
            self.client is not None,
            query,
            limit,
            status=state.status if state else "idle",
            source_availability=source.state.availability if source else None,
            native_commands=callable(getattr(self.client, "command_request", None)),
            plan_available=callable(getattr(self.client, "set_plan_mode", None)),
        )

    async def execute_command(
        self,
        session_id: str,
        command: str,
        external_session_id: str | None = None,
        raw: str | None = None,
        args: tuple[str, ...] = (),
    ) -> RuntimeCommandResult:
        requested = command.removeprefix("/") if isinstance(command, str) else ""
        try:
            name, text = command_input(command, raw, args)
            descriptor = next(
                (
                    c
                    for c in self.catalog(session_id, external_session_id)
                    if c.id == name
                ),
                None,
            )
            if descriptor is None:
                return RuntimeCommandResult(
                    command=requested,
                    ok=False,
                    code="unknown_command",
                    message=f"Unknown Codex command: /{requested}",
                )
            payload = parse_payload(name, text)
        except (ValueError, TypeError) as exc:
            return RuntimeCommandResult(
                command=requested, ok=False, code="invalid_command", message=str(exc)
            )
        if not descriptor.enabled:
            return RuntimeCommandResult(
                command=requested,
                ok=False,
                code="command_unavailable",
                message=descriptor.disabled_reason,
            )
        if descriptor.metadata["ui"]["kind"] == "selector":
            return RuntimeCommandResult(
                command=requested,
                ok=False,
                code="command_requires_selector",
                message="Use the session selection control.",
            )
        await self.ensure_started()
        try:
            result, execution = await self.dispatch(
                session_id, external_session_id, name, payload
            )
            if result.get("ok") is False or result.get("applied") is False:
                return RuntimeCommandResult(
                    command=requested,
                    ok=False,
                    code="command_error",
                    message="Native command was not applied.",
                    result={**result, "executionState": "completed"},
                )
            return RuntimeCommandResult(
                command=requested, result={**result, "executionState": execution}
            )
        except CommandUnavailable as exc:
            return RuntimeCommandResult(
                command=requested,
                ok=False,
                code="command_unavailable",
                message=str(exc),
            )
        except (
            RuntimeConflictError,
            RuntimeInvalidRequestError,
            InvalidParamsError,
            InvalidRequestError,
            MethodNotFoundError,
        ) as exc:
            return RuntimeCommandResult(
                command=requested,
                ok=False,
                code="command_rejected",
                message=str(exc),
                result={"executionState": "completed"},
            )
        except Exception:  # noqa: BLE001 - post-dispatch ambiguity must not be retried
            return RuntimeCommandResult(
                command=requested,
                ok=False,
                code="command_outcome_unknown",
                message="Command outcome is unknown. Refresh before taking further action.",
                result={"executionState": "unknown", "retryable": False},
            )

    async def dispatch(
        self, session_id: str, thread_id: str, name: str, payload: Any
    ) -> tuple[dict[str, Any], str]:
        if name == "status":
            state = self.states.get(session_id)
            status = state.status if state else "unknown"
            return {
                "status": status,
                "externalSessionId": thread_id,
                "text": f"Codex: {status}",
            }, "completed"
        if name == "compact":
            result = await self.client.compact_thread(thread_id)
            return dict(result.payload), "accepted"
        if name == "plan":
            result = dict(await self.client.set_plan_mode(thread_id, payload))
            if type(result.get("applied")) is not bool:
                raise ValueError("Native plan acknowledgement is incomplete")
            return {
                **result,
                "text": f"Planning mode {'on' if payload == 'plan' else 'off'}. Applies to subsequent turns.",
            }, "completed"
        if name == "review":
            result = await self.client.command_request(
                thread_id, "review/start", {"target": payload, "delivery": "inline"}
            )
            if (
                result.get("reviewThreadId") != thread_id
                or not isinstance(result.get("turn"), dict)
                or not isinstance(result["turn"].get("id"), str)
                or not result["turn"]["id"]
            ):
                raise ValueError("Review acknowledgement is missing its physical turn")
            return result, "accepted"
        return await self.execute_goal(thread_id, payload)

    async def execute_goal(
        self, thread_id: str, payload: tuple[str, dict[str, Any]]
    ) -> tuple[dict[str, Any], str]:
        # Keep the native read/check/write together for concurrent AA callers.
        # Weak entries disappear when there are no executing or waiting callers.
        lock = self._goal_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            return await self._execute_goal(thread_id, payload)

    async def _execute_goal(
        self, thread_id: str, payload: tuple[str, dict[str, Any]]
    ) -> tuple[dict[str, Any], str]:
        action, fields = payload
        observation = await self.client.command_request(
            thread_id, "thread/goal/get", {}
        )
        current = validated_goal(observation, thread_id, nullable=True)
        if action == "status":
            return {"goal": current, "text": goal_text(current)}, "completed"
        if (
            action == "create"
            and current is not None
            and current["status"] != "complete"
        ):
            raise CommandUnavailable(
                "An unfinished goal exists. Use /goal edit to change it."
            )
        if action in {"edit", "pause", "resume", "clear", "budget"} and current is None:
            raise CommandUnavailable("No native goal exists.")
        if action == "pause" and current["status"] != "active":
            raise CommandUnavailable("The goal is not active.")
        if action == "resume" and current["status"] not in {
            "paused",
            "blocked",
            "usageLimited",
            "budgetLimited",
        }:
            raise CommandUnavailable(
                "The goal cannot be resumed from its current status."
            )
        if action == "clear":
            result = await self.client.command_request(
                thread_id, "thread/goal/clear", {}
            )
            if type(result.get("cleared")) is not bool:
                raise ValueError("Native clear acknowledgement is incomplete")
            return {
                **result,
                "ok": result["cleared"],
                "text": "Goal cleared."
                if result["cleared"]
                else "Goal was not cleared.",
            }, "completed"
        result = await self.client.command_request(thread_id, "thread/goal/set", fields)
        observed = validated_goal(result, thread_id)
        return {
            **result,
            "goal": observed,
            "text": goal_text(observed),
        }, "accepted" if fields.get("status") == "active" else "completed"


def validated_goal(
    result: dict[str, Any], thread_id: str, *, nullable: bool = False
) -> dict[str, Any] | None:
    if not isinstance(result, dict) or "goal" not in result:
        raise ValueError("Native goal was not observed")
    if result["goal"] is None and nullable:
        return None
    goal = ThreadGoal.model_validate(result["goal"])
    if goal.thread_id != thread_id:
        raise ValueError("Native goal belongs to another thread")
    return goal.model_dump(mode="json", by_alias=True)


def goal_text(goal: dict[str, Any] | None) -> str:
    if goal is None:
        return "No goal is set for this session."
    budget = f" / {goal['tokenBudget']}" if goal.get("tokenBudget") is not None else ""
    return f"{goal['objective']}\n{goal['status']} · {goal['tokensUsed']}{budget} tokens · {goal['timeUsedSeconds']}s"
