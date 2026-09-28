"""Real SDK stdio transport, with only the app-server process replaced."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import openai_codex
import pytest
from openai_codex import (
    AsyncCodex,
    CodexConfig,
    MethodNotFoundError,
    TransportClosedError,
)

from connector.runtime_protocol import RuntimeConflictError, RuntimeInvalidRequestError
from connector.runtimes.codex.sdk.client import CodexSdkClient
from connector.runtimes.codex.sdk.events import CodexSdkEvent
from connector.runtimes.codex.sdk.runtime_client import (
    CodexInterruptTurnRequest,
    CodexStartThreadRequest,
    CodexStartTurnRequest,
)

SERVER = r"""
import json, sys, time
from pathlib import Path
root = Path(sys.argv[1])
pending = {}
def send(value):
    print(json.dumps(value), flush=True)
def thread(id):
    return {"id":id,"cliVersion":"0.144.4","createdAt":1,"cwd":str(root),
        "ephemeral":False,"modelProvider":"openai","preview":"","sessionId":"session","source":"appServer",
        "status":{"type":"idle"},"turns":[],"updatedAt":2}
def turn(id, status="inProgress"):
    return {"id":id,"status":status,"items":[],"completedAt":None,"durationMs":None,
        "error":None,"itemsView":None,"startedAt":None}
for line in sys.stdin:
    request = json.loads(line)
    with (root / "requests.jsonl").open("a") as log:
        log.write(json.dumps(request) + "\n")
    if "id" not in request:
        continue
    method, params = request["method"], request.get("params", {})
    config = json.loads((root / "control.json").read_text())
    if method in config.get("disconnect", []):
        sys.exit(0)
    if method in config.get("hang", []):
        continue
    if method in config.get("errors", {}):
        send({"id":request["id"],"error":config["errors"][method]})
        continue
    for event in config.get("before", {}).get(method, []):
        send(event)
    if method in config.get("before", {}):
        time.sleep(0.04)
    if method == "initialize":
        result = {"userAgent":"codex/0.144.4"}
    elif method in ("thread/start", "thread/resume"):
        result = {"thread":thread(params.get("threadId", "thread")),"model":"native-model",
            "modelProvider":"openai","reasoningEffort":None,"cwd":str(root),
            "approvalPolicy":"on-request","approvalsReviewer":"user","sandbox":{"type":"dangerFullAccess"}}
        result.update(config.get("resume", {}))
        if "resume_thread_id" in config:
            result["thread"]["id"] = config["resume_thread_id"]
        for key in config.get("omit_resume", []):
            result.pop(key, None)
    elif method == "thread/goal/get":
        result = {"goal":None}
    elif method == "thread/goal/set":
        result = {"goal":{"threadId":"thread","objective":"objective","status":"active",
            "createdAt":1,"updatedAt":2,"tokensUsed":0,"timeUsedSeconds":0,"tokenBudget":None}}
    elif method == "thread/goal/clear":
        result = {"cleared":True}
    elif method == "review/start":
        result = {"reviewThreadId":"thread","turn":turn("review")}
    elif method == "turn/start":
        result = {"turn":turn("ordinary")}
    else:
        result = {}
    result = config.get("results", {}).get(method, result)
    response = {"id":request["id"],"result":result}
    if method in config.get("hold", []):
        pending[method] = response
        continue
    send(response)
    for held in config.get("release_after", {}).get(method, []):
        send(pending.pop(held))
"""


class Wire:
    def __init__(self, root: Path):
        self.root = root
        self.configure()
        self.events: list[CodexSdkEvent] = []

    def configure(self, **values):
        (self.root / "control.json").write_text(json.dumps(values))

    @property
    def requests(self):
        return [
            json.loads(line)
            for line in (self.root / "requests.jsonl").read_text().splitlines()
        ]

    def calls(self, method):
        return [
            request["params"]
            for request in self.requests
            if request["method"] == method
        ]

    async def notified(self, count):
        async with asyncio.timeout(2):
            while len(self.events) < count:
                await asyncio.sleep(0.001)


@asynccontextmanager
async def fixture(tmp_path):
    wire = Wire(tmp_path)
    server = tmp_path / "app_server.py"
    server.write_text(SERVER)
    sdk = AsyncCodex(
        CodexConfig(
            launch_args_override=(sys.executable, "-u", str(server), str(tmp_path))
        )
    )
    client = CodexSdkClient(sdk, sdk=openai_codex)

    async def receive(message):
        wire.events.append(
            message
            if isinstance(message, CodexSdkEvent)
            else CodexSdkEvent.from_value(message)
        )

    await client.start(receive)
    try:
        yield client, wire, sdk
    finally:
        # A failing routing test must also release SDK worker threads whose
        # dedicated queue might otherwise be unregistered during cancellation.
        for pending in list(sdk._client._sync._router._turn_notifications.values()):
            pending.put(TransportClosedError("test fixture cleanup"))
        await client.stop()


def lifecycle(turn_id, *, completed=False):
    return {
        "method": "turn/completed" if completed else "turn/started",
        "params": {
            "threadId": "thread",
            "turn": {
                "id": turn_id,
                "status": "completed" if completed else "inProgress",
                "items": [],
                "completedAt": None,
                "durationMs": None,
                "error": None,
                "itemsView": None,
                "startedAt": None,
            },
        },
    }


def settings(model, effort):
    return {
        "method": "thread/settings/updated",
        "params": {
            "threadId": "thread",
            "threadSettings": {
                "model": model,
                "effort": effort,
                "modelProvider": "openai",
                "cwd": "/tmp",
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "sandboxPolicy": {"type": "dangerFullAccess"},
                "collaborationMode": {
                    "mode": "default",
                    "settings": {
                        "model": model,
                        "reasoning_effort": effort,
                        "developer_instructions": None,
                    },
                },
            },
        },
    }


def test_raw_goal_budget_null_and_read_only_get_use_native_transport(tmp_path):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            assert await client.command_request("thread", "thread/goal/get", {}) == {
                "goal": None
            }
            assert not wire.calls("thread/resume")
            result = await client.command_request(
                "thread", "thread/goal/set", {"tokenBudget": None}
            )
            assert result["goal"]["threadId"] == "thread"
            await client.command_request("thread", "thread/goal/clear", {})
            assert wire.calls("thread/goal/set") == [
                {"threadId": "thread", "tokenBudget": None}
            ]
            assert len(wire.calls("thread/resume")) == 1
            assert not wire.calls("turn/start")

    asyncio.run(run())


@pytest.mark.parametrize(
    "method", ["thread/goal/set", "thread/goal/clear", "review/start", "plan"]
)
def test_native_writer_conflict_prevents_all_command_mutation(tmp_path, method):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            wire.configure(
                errors={
                    "thread/resume": {
                        "code": -32600,
                        "message": "thread already has an active writer",
                    }
                }
            )
            with pytest.raises(RuntimeConflictError):
                if method == "plan":
                    await client.set_plan_mode("thread", "plan")
                else:
                    await client.command_request(
                        "thread",
                        method,
                        {"delivery": "inline"} if method == "review/start" else {},
                    )
            assert not wire.calls(
                "thread/settings/update" if method == "plan" else method
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "method,params",
    [
        ("turn/start", {}),
        ("thread/goal/get", {"threadId": "other"}),
        ("thread/goal/set", {"thread_id": "other"}),
        ("review/start", {"delivery": "detached"}),
    ],
)
def test_command_seam_rejects_method_or_identity_overrides_before_rpc(
    tmp_path, method, params
):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            with pytest.raises(RuntimeInvalidRequestError):
                await client.command_request("thread", method, params)
            assert not wire.calls("thread/resume")
            assert not wire.calls(method)

    asyncio.run(run())


@pytest.mark.parametrize("start", [False, True])
def test_plan_payload_uses_authoritative_native_model_and_explicit_null_effort(
    tmp_path, start
):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            if start:
                await client.start_thread(
                    CodexStartThreadRequest(model="requested-model")
                )
            assert await client.set_plan_mode("thread", "plan") == {"applied": True}
            assert wire.calls("thread/settings/update") == [
                {
                    "threadId": "thread",
                    "collaborationMode": {
                        "mode": "plan",
                        "settings": {
                            "model": "native-model",
                            "reasoning_effort": None,
                            "developer_instructions": None,
                        },
                    },
                }
            ]
            assert len(wire.calls("thread/resume")) == (0 if start else 1)
            assert not wire.calls("turn/start")

    asyncio.run(run())


def test_plan_refuses_missing_authoritative_effort_without_guessing(tmp_path):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            wire.configure(omit_resume=["reasoningEffort"])
            with pytest.raises(RuntimeInvalidRequestError):
                await client.set_plan_mode("thread", "plan")
            assert not wire.calls("thread/settings/update")

    asyncio.run(run())


def test_wrong_resume_identity_cannot_grant_writer_or_send_command(tmp_path):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            wire.configure(resume_thread_id="another-thread")
            with pytest.raises(RuntimeError, match="identity"):
                await client.command_request("thread", "thread/goal/clear", {})
            assert not wire.calls("thread/goal/clear")
            wire.configure()
            await client.command_request("thread", "thread/goal/clear", {})
            assert len(wire.calls("thread/resume")) == 2

    asyncio.run(run())


def test_settings_notifications_win_over_older_resume_and_update_ack(tmp_path):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            wire.configure(
                before={
                    "thread/resume": [settings("new-model", "high")],
                    "thread/settings/update": [settings("latest-model", None)],
                }
            )
            await client.set_plan_mode("thread", "plan")
            wire.configure()
            await client.set_plan_mode("thread", "default")
            payloads = wire.calls("thread/settings/update")
            assert payloads[0]["collaborationMode"]["settings"]["model"] == "new-model"
            assert (
                payloads[0]["collaborationMode"]["settings"]["reasoning_effort"]
                == "high"
            )
            assert payloads[1]["collaborationMode"]["settings"] == {
                "model": "latest-model",
                "reasoning_effort": None,
                "developer_instructions": None,
            }

    asyncio.run(run())


@pytest.mark.parametrize(
    "ack",
    [{"applied": None}, {"applied": 1}, {"applied": "true"}, {"unexpected": True}],
)
def test_plan_rejects_malformed_acknowledgements_without_replay(tmp_path, ack):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            wire.configure(results={"thread/settings/update": ack})
            with pytest.raises(ValueError):
                await client.set_plan_mode("thread", "plan")
            assert len(wire.calls("thread/settings/update")) == 1

    asyncio.run(run())


def test_unsupported_native_plan_method_remains_a_visible_rejection(tmp_path):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            wire.configure(
                errors={
                    "thread/settings/update": {
                        "code": -32601,
                        "message": "unsupported experimental method",
                    }
                }
            )
            with pytest.raises(MethodNotFoundError):
                await client.set_plan_mode("thread", "plan")
            assert len(wire.calls("thread/settings/update")) == 1
            assert not wire.calls("turn/start")

    asyncio.run(run())


def test_explicit_plan_nonapplication_is_preserved(tmp_path):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            wire.configure(results={"thread/settings/update": {"applied": False}})
            assert await client.set_plan_mode("thread", "plan") == {"applied": False}
            assert len(wire.calls("thread/settings/update")) == 1

    asyncio.run(run())


@pytest.mark.parametrize("disconnect", [False, True])
def test_postdispatch_transport_loss_or_timeout_never_replays_command(
    tmp_path, disconnect
):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            wire.configure(
                **{"disconnect" if disconnect else "hang": ["thread/goal/set"]}
            )
            with pytest.raises((TimeoutError, TransportClosedError)):
                async with asyncio.timeout(0.1):
                    await client.command_request(
                        "thread", "thread/goal/set", {"status": "active"}
                    )
            assert len(wire.calls("thread/goal/set")) == 1

    asyncio.run(run())


def test_native_goal_turns_before_ack_and_followup_remain_visible_and_interruptible(
    tmp_path,
):
    async def run():
        async with fixture(tmp_path) as (client, wire, sdk):
            wire.configure(before={"thread/goal/set": [lifecycle("goal-1")]})
            await client.command_request(
                "thread", "thread/goal/set", {"objective": "test", "status": "active"}
            )
            await wire.notified(1)
            await client.interrupt_turn(CodexInterruptTurnRequest("thread", "goal-1"))
            wire.configure(
                before={
                    "thread/goal/get": [
                        lifecycle("goal-2"),
                        lifecycle("goal-1", completed=True),
                    ]
                }
            )
            await client.command_request("thread", "thread/goal/get", {})
            await wire.notified(3)
            with pytest.raises(RuntimeInvalidRequestError):
                await client.interrupt_turn(
                    CodexInterruptTurnRequest("thread", "goal-1")
                )
            await client.interrupt_turn(CodexInterruptTurnRequest("thread", "goal-2"))
            assert wire.calls("turn/interrupt") == [
                {"threadId": "thread", "turnId": "goal-1"},
                {"threadId": "thread", "turnId": "goal-2"},
            ]
            assert [event.event_type for event in wire.events] == [
                "turn/started",
                "turn/started",
                "turn/completed",
            ]
            assert sdk._client._sync._router._pending_turn_notifications == {}

    asyncio.run(run())


def test_fast_inline_review_completion_before_ack_does_not_resurrect_a_turn(tmp_path):
    async def run():
        async with fixture(tmp_path) as (client, wire, sdk):
            wire.configure(
                before={
                    "review/start": [
                        lifecycle("review"),
                        lifecycle("review", completed=True),
                    ]
                }
            )
            result = await client.command_request(
                "thread",
                "review/start",
                {"target": {"type": "uncommittedChanges"}, "delivery": "inline"},
            )
            assert result["turn"]["id"] == "review"
            await wire.notified(2)
            with pytest.raises(RuntimeInvalidRequestError):
                await client.interrupt_turn(
                    CodexInterruptTurnRequest("thread", "review")
                )
            assert len(wire.events) == 2
            assert sdk._client._sync._router._pending_turn_notifications == {}

    asyncio.run(run())


def test_ordinary_turn_after_command_keeps_one_stream_and_no_native_queue_leak(
    tmp_path,
):
    async def run():
        async with fixture(tmp_path) as (client, wire, sdk):
            await client.command_request("thread", "thread/goal/clear", {})
            wire.configure(
                before={
                    "turn/start": [
                        lifecycle("ordinary"),
                        lifecycle("ordinary", completed=True),
                    ]
                }
            )
            await client.start_turn(CodexStartTurnRequest("thread", "hello"))
            await wire.notified(2)
            await asyncio.sleep(0.02)
            assert [event.event_type for event in wire.events] == [
                "turn/started",
                "turn/completed",
            ]
            assert sdk._client._sync._router._pending_turn_notifications == {}
            assert sdk._client._sync._router._turn_notifications == {}

    asyncio.run(run())


def test_first_command_during_ordinary_turn_preserves_its_stream_and_interrupt(
    tmp_path,
):
    async def run():
        async with fixture(tmp_path) as (client, wire, sdk):
            wire.configure(before={"turn/start": [lifecycle("ordinary")]})
            await client.start_turn(CodexStartTurnRequest("thread", "hello"))
            await wire.notified(1)
            wire.configure()
            await client.command_request(
                "thread", "thread/goal/set", {"status": "paused"}
            )
            await client.interrupt_turn(CodexInterruptTurnRequest("thread", "ordinary"))
            wire.configure(
                before={
                    "thread/goal/get": [
                        {
                            "method": "item/agentMessage/delta",
                            "params": {
                                "threadId": "thread",
                                "turnId": "ordinary",
                                "itemId": "answer",
                                "delta": "answer",
                            },
                        },
                        lifecycle("ordinary", completed=True),
                    ]
                }
            )
            await client.command_request("thread", "thread/goal/get", {})
            await wire.notified(3)
            await asyncio.sleep(0.01)
            assert [event.event_type for event in wire.events] == [
                "turn/started",
                "item/agentMessage/delta",
                "turn/completed",
            ]
            assert wire.calls("turn/interrupt") == [
                {"threadId": "thread", "turnId": "ordinary"}
            ]
            assert client._stream_tasks == {}
            assert sdk._client._sync._router._turn_notifications == {}
            assert sdk._client._sync._router._pending_turn_notifications == {}

    asyncio.run(run())


def test_first_command_while_ordinary_start_ack_is_pending_preserves_early_events(
    tmp_path,
):
    async def run():
        async with fixture(tmp_path) as (client, wire, sdk):
            wire.configure(
                hold=["turn/start"],
                release_after={"thread/goal/set": ["turn/start"]},
                before={
                    "turn/start": [lifecycle("ordinary")],
                    "thread/goal/set": [
                        {
                            "method": "item/agentMessage/delta",
                            "params": {
                                "threadId": "thread",
                                "turnId": "ordinary",
                                "itemId": "answer",
                                "delta": "early",
                            },
                        }
                    ],
                },
            )
            starting = asyncio.create_task(
                client.start_turn(CodexStartTurnRequest("thread", "hello"))
            )
            async with asyncio.timeout(2):
                while not wire.calls("turn/start"):
                    await asyncio.sleep(0.001)
            await client.command_request(
                "thread", "thread/goal/set", {"status": "paused"}
            )
            await starting
            wire.configure(
                before={"thread/goal/get": [lifecycle("ordinary", completed=True)]}
            )
            await client.command_request("thread", "thread/goal/get", {})
            await wire.notified(3)
            assert [event.event_type for event in wire.events] == [
                "turn/started",
                "item/agentMessage/delta",
                "turn/completed",
            ]
            assert client._stream_tasks == {}
            assert sdk._client._sync._router._turn_notifications == {}
            assert sdk._client._sync._router._pending_turn_notifications == {}

    asyncio.run(run())


@pytest.mark.parametrize("disconnect", [False, True])
def test_stop_and_disconnect_invalidate_native_writer_and_turn_ownership(
    tmp_path, disconnect
):
    async def run():
        async with fixture(tmp_path) as (client, wire, _):
            wire.configure(before={"thread/goal/set": [lifecycle("goal")]})
            await client.command_request(
                "thread", "thread/goal/set", {"status": "active"}
            )
            await wire.notified(1)
            if disconnect:
                wire.configure(disconnect=["thread/goal/get"])
                with pytest.raises(TransportClosedError):
                    await client.command_request("thread", "thread/goal/get", {})
                async with asyncio.timeout(2):
                    while client._loaded_thread_ids:
                        await asyncio.sleep(0.001)
            else:
                await client.stop()
            assert client._loaded_thread_ids == set()
            assert client._threads == {} and client._turns == {}

    asyncio.run(run())
