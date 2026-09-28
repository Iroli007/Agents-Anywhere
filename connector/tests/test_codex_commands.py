"""Exercise commands through the public runtime without a desktop IPC owner."""

import asyncio
from copy import deepcopy

import pytest
from test_codex_runtime import FakeCodexClient, FakeHost, _config

from connector.runtime_protocol import RuntimeConflictError
from connector.runtimes.codex.runtime import CodexRuntime


def goal(status="active", **values):
    return {
        "threadId": "thread_1",
        "objective": "Fix the tests",
        "status": status,
        "createdAt": 1,
        "updatedAt": 2,
        "tokensUsed": 42,
        "timeUsedSeconds": 3,
        "tokenBudget": None,
        **values,
    }


class NativeClient(FakeCodexClient):
    def __init__(self):
        super().__init__()
        self.results.update(
            {
                "thread/goal/get": {"goal": None},
                "thread/goal/set": {"goal": goal()},
                "thread/goal/clear": {"cleared": True},
                "review/start": {
                    "reviewThreadId": "thread_1",
                    "turn": {"id": "review_1"},
                },
                "thread/settings/update": {"applied": True},
            }
        )

    async def command_request(self, thread_id, method, params):
        return deepcopy(self.record_request(method, {"threadId": thread_id, **params}))

    async def set_plan_mode(self, thread_id, mode):
        return self.record_request(
            "thread/settings/update",
            {
                "threadId": thread_id,
                "collaborationMode": mode,
            },
        )


async def runtime_fixture(status="idle", client=None):
    client = client or NativeClient()
    host = FakeHost()
    runtime = CodexRuntime(config=_config(), host=host, client=client)
    await runtime._session_states.update("sess_1", "thread_1", status=status)
    return runtime, client, host


def run(coro):
    return asyncio.run(coro)


def test_catalog_and_capabilities_enable_native_commands_without_ipc():
    async def check():
        runtime, client, _ = await runtime_fixture()
        catalog = {c.id: c for c in await runtime.list_commands("sess_1", "thread_1")}
        assert set(catalog) == {
            "status",
            "compact",
            "goal",
            "review",
            "plan",
            "model",
            "reasoning",
            "permission",
        }
        assert all(c.enabled for c in catalog.values())
        assert catalog["model"].metadata["ui"] == {
            "kind": "selector",
            "target": "model",
        }
        assert catalog["goal"].accepts_args
        assert catalog["goal"].metadata["ui"]["acceptsMultiline"] is True
        assert [
            c.id
            for c in await runtime.list_commands("sess_1", "thread_1", "plan-mode", 1)
        ] == ["plan"]
        cap = next(
            c
            for c in (
                await runtime.get_session_capabilities("sess_1", "thread_1")
            ).capabilities
            if c.capability_id == "session.commands"
        )
        assert cap.supported and cap.available
        assert client.requests == []

    run(check())


@pytest.mark.parametrize(
    "command,raw,args",
    [
        ("compact", "", ()),
        ("compact", "/review", ()),
        ("compact", "/compact extra", ()),
        ("plan", "/plan maybe", ()),
        ("goal", "/goal budget true", ()),
        ("goal", "/goal budget -1", ()),
        ("goal", "/goal pause extra", ()),
        ("goal", "/goal set []", ()),
        ("goal", '/goal set {"threadId":"other"}', ()),
        ("review", "/review branch", ()),
        ("compact", None, (1,)),
        ("goal", None, ("create", "objective")),
    ],
)
def test_invalid_command_input_never_reaches_native_client(command, raw, args):
    async def check():
        runtime, client, _ = await runtime_fixture()
        result = await runtime.execute_command("sess_1", command, "thread_1", raw, args)
        assert result.ok is False and result.code == "invalid_command"
        assert client.requests == []

    run(check())


def test_busy_policy_blocks_compact_but_allows_goal_pause():
    async def check():
        runtime, client, _ = await runtime_fixture("running")
        client.results["thread/goal/get"] = {"goal": goal()}
        client.results["thread/goal/set"] = {"goal": goal("paused")}
        blocked = await runtime.execute_command("sess_1", "compact", "thread_1")
        assert not blocked.ok and blocked.code == "command_unavailable"
        paused = await runtime.execute_command(
            "sess_1", "goal", "thread_1", "/goal pause"
        )
        assert paused.ok and paused.result["goal"]["status"] == "paused"
        assert client.requests[-1] == (
            "thread/goal/set",
            {"threadId": "thread_1", "status": "paused"},
        )
        assert all(method != "thread/compact/start" for method, _ in client.requests)

    run(check())


@pytest.mark.parametrize(
    "availability", ["archived", "deleted", "unavailable", "missing"]
)
@pytest.mark.parametrize(
    "command,raw",
    [
        ("compact", "/compact"),
        ("review", "/review"),
        ("goal", "/goal create Fix tests"),
        ("plan", "/plan on"),
    ],
)
def test_unavailable_source_blocks_native_commands(availability, command, raw):
    async def check():
        runtime, client, _ = await runtime_fixture()
        await runtime._source_states.update(
            session_id="sess_1",
            external_session_id="thread_1",
            availability=availability,
            reason=None,
            observed_at=None,
            observation_origin="event",
        )
        result = await runtime.execute_command("sess_1", command, "thread_1", raw)
        assert not result.ok and result.code == "command_unavailable"
        assert result.message == f"session_{availability}"
        assert client.requests == []
        catalog = {c.id: c for c in await runtime.list_commands("sess_1", "thread_1")}
        assert not catalog[command].enabled
        assert catalog["status"].enabled

    run(check())


def test_compact_is_accepted_and_never_sent_as_user_text():
    async def check():
        runtime, client, _ = await runtime_fixture()
        result = await runtime.execute_command(
            "sess_1", "compact-thread", "thread_1", "/compact-thread"
        )
        assert result.ok and result.result["executionState"] == "accepted"
        assert client.requests[-1] == ("thread/compact/start", {"threadId": "thread_1"})
        assert not any(method == "turn/start" for method, _ in client.requests)

    run(check())


@pytest.mark.parametrize(
    "text", ["create Fix  the tests\nkeep spacing", "Fix  the tests\nkeep spacing"]
)
def test_goal_creation_preserves_objective_without_fabricating_a_budget(text):
    async def check():
        runtime, client, _ = await runtime_fixture()
        result = await runtime.execute_command(
            "sess_1", "goal", "thread_1", "/goal " + text
        )
        assert result.ok and result.result["executionState"] == "accepted"
        assert client.requests[-1] == (
            "thread/goal/set",
            {
                "threadId": "thread_1",
                "objective": "Fix  the tests\nkeep spacing",
                "status": "active",
            },
        )

    run(check())


@pytest.mark.parametrize(
    "text,fields",
    [
        (
            'create {"objective":"Fix tests","tokenBudget":1000}',
            {"objective": "Fix tests", "tokenBudget": 1000, "status": "active"},
        ),
        ("budget null", {"tokenBudget": None}),
        ("budget 512", {"tokenBudget": 512}),
    ],
)
def test_goal_budget_reaches_native_protocol_exactly(text, fields):
    async def check():
        runtime, client, _ = await runtime_fixture()
        if not text.startswith("create"):
            client.results["thread/goal/get"] = {"goal": goal()}
        result = await runtime.execute_command(
            "sess_1", "goal", "thread_1", args=(text,)
        )
        assert result.ok
        assert client.requests[-1] == (
            "thread/goal/set",
            {"threadId": "thread_1", **fields},
        )

    run(check())


def test_existing_goal_requires_explicit_edit_and_status_is_visible():
    async def check():
        runtime, client, _ = await runtime_fixture()
        client.results["thread/goal/get"] = {"goal": goal()}
        result = await runtime.execute_command(
            "sess_1", "goal", "thread_1", "/goal another objective"
        )
        assert not result.ok and result.code == "command_unavailable"
        assert not any(m == "thread/goal/set" for m, _ in client.requests)
        result = await runtime.execute_command("sess_1", "goal", "thread_1", "/goal")
        assert result.ok and "Fix the tests" in result.result["text"]
        assert "42" in result.result["text"]

    run(check())


def test_concurrent_goal_creation_does_not_replace_an_unfinished_goal():
    class YieldingGoalClient(NativeClient):
        async def command_request(self, thread_id, method, params):
            response = await super().command_request(thread_id, method, params)
            if method == "thread/goal/get":
                await asyncio.sleep(0)
            if method == "thread/goal/set":
                response = {"goal": goal(objective=params["objective"])}
                self.results["thread/goal/get"] = response
            return response

    async def check():
        runtime, client, _ = await runtime_fixture(client=YieldingGoalClient())
        first, second = await asyncio.gather(
            runtime.execute_command("sess_1", "goal", "thread_1", "/goal First task"),
            runtime.execute_command("sess_1", "goal", "thread_1", "/goal Second task"),
        )
        assert first.ok
        assert not second.ok and second.code == "command_unavailable"
        assert sum(method == "thread/goal/set" for method, _ in client.requests) == 1
        assert client.results["thread/goal/get"]["goal"]["objective"] == "First task"

    run(check())


@pytest.mark.parametrize("terminal", ["turn/completed", "turn/failed"])
def test_late_terminal_does_not_hide_a_new_command_turn(terminal):
    async def check():
        runtime, _, host = await runtime_fixture()
        for turn_id in ("ordinary", "goal-followup"):
            await runtime._handle_notification(
                {
                    "method": "turn/started",
                    "params": {"threadId": "thread_1", "turn": {"id": turn_id}},
                }
            )
        await runtime._handle_notification(
            {
                "method": terminal,
                "params": {"threadId": "thread_1", "turn": {"id": "ordinary"}},
            }
        )
        assert runtime._active_turn_ids["sess_1"] == "goal-followup"
        assert runtime._session_states.get("sess_1").status == "running"
        assert host.turn_ends[-1]["turn_id"] == "ordinary"

    run(check())


@pytest.mark.parametrize(
    "text,target",
    [
        ("", {"type": "uncommittedChanges"}),
        ("branch main", {"type": "baseBranch", "branch": "main"}),
        ("commit abc123", {"type": "commit", "sha": "abc123"}),
        (
            "custom Find  bugs\nonly",
            {"type": "custom", "instructions": "Find  bugs\nonly"},
        ),
    ],
)
def test_review_uses_native_inline_review(text, target):
    async def check():
        runtime, client, _ = await runtime_fixture()
        result = await runtime.execute_command(
            "sess_1", "review", "thread_1", args=(text,)
        )
        assert result.ok and result.result["executionState"] == "accepted"
        assert client.requests[-1] == (
            "review/start",
            {"threadId": "thread_1", "target": target, "delivery": "inline"},
        )

    run(check())


@pytest.mark.parametrize(
    "raw,mode", [("/plan", "plan"), ("/plan on", "plan"), ("/plan off", "default")]
)
def test_plan_switch_changes_native_settings_without_starting_a_turn(raw, mode):
    async def check():
        runtime, client, _ = await runtime_fixture()
        result = await runtime.execute_command("sess_1", "plan", "thread_1", raw)
        assert result.ok and result.result["executionState"] == "completed"
        assert client.requests[-1] == (
            "thread/settings/update",
            {"threadId": "thread_1", "collaborationMode": mode},
        )
        assert not any(m == "turn/start" for m, _ in client.requests)

    run(check())


@pytest.mark.parametrize(
    "method,value",
    [
        ("review/start", {}),
        ("review/start", {"reviewThreadId": "other", "turn": {"id": "turn"}}),
        ("thread/goal/set", {"goal": None}),
        ("thread/settings/update", {}),
        ("thread/compact/start", TimeoutError("lost acknowledgement")),
    ],
)
def test_ambiguous_dispatch_never_claims_success_or_retries(method, value):
    async def check():
        runtime, client, _ = await runtime_fixture()
        client.results[method] = value
        name, raw = {
            "review/start": ("review", "/review"),
            "thread/goal/set": ("goal", "/goal create test"),
            "thread/settings/update": ("plan", "/plan on"),
            "thread/compact/start": ("compact", "/compact"),
        }[method]
        result = await runtime.execute_command("sess_1", name, "thread_1", raw)
        assert not result.ok and result.code == "command_outcome_unknown"
        assert result.result == {"executionState": "unknown", "retryable": False}
        assert sum(m == method for m, _ in client.requests) == 1

    run(check())


def test_other_native_writer_rejects_command_without_prompt_fallback():
    async def check():
        runtime, client, _ = await runtime_fixture()
        client.results["thread/compact/start"] = RuntimeConflictError("active writer")
        result = await runtime.execute_command("sess_1", "compact", "thread_1")
        assert not result.ok and result.code == "command_rejected"
        assert "active writer" in result.message
        assert not any(m == "turn/start" for m, _ in client.requests)

    run(check())
