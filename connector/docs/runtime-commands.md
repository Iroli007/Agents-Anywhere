# Runtime slash commands

Enter `/` in a connected session to load that runtime's command catalog. Commands
run through the session command API. Their input is never sent as a normal model
message when lookup, validation or execution fails.

Selecting a command that accepts arguments inserts an editable draft. Submit the
draft to execute it. The request preserves its original text, including whitespace
and newlines; attachments remain in the draft and are not sent with commands.

## Codex

These commands use the native SDK session owned by AA:

| Command | Behavior |
| --- | --- |
| `/status` | Show the observed session status. |
| `/compact` | Request native context compaction. Alias: `/compact-thread`. |
| `/goal` or `/goal status` | Read the native objective, status, token usage and elapsed time. |
| `/goal <objective>` or `/goal create <objective>` | Create an active goal when no unfinished goal exists. |
| `/goal edit <objective>` | Update an existing goal's objective. |
| `/goal pause`, `/goal resume`, `/goal clear` | Explicitly change the native goal lifecycle. |
| `/goal budget 100000` or `/goal budget null` | Set or remove the native token budget. |
| `/review` | Review uncommitted changes in the current session. |
| `/review branch main`, `/review commit <sha>` | Review against a branch or review a commit. |
| `/review custom <instructions>` | Start a review with free-form instructions. |
| `/plan on`, `/plan off` | Change native planning mode for subsequent turns. Bare `/plan` means `on`; alias: `/plan-mode`. |
| `/model`, `/reasoning`, `/permission` | Open the existing selection controls. Alias: `/permissions`. |

Goals also accept JSON through `create`, `edit` and `set`, for example:

```text
/goal create {"objective":"Fix the failing tests","tokenBudget":100000}
/goal set {"status":"paused"}
```

Only `objective`, `status` and `tokenBudget` are accepted. Omitted budgets remain
omitted; the connector does not invent a budget. Creating a second unfinished
goal requires an explicit edit or clear. Concurrent goal commands in the same
connector are serialized around the native read/check/write operation.

Compaction, review and planning mode require an idle or failed turn. Goal commands
can run while the session is active, so `/goal pause` remains usable. Commands
respect known archived, missing, deleted and unavailable source states. A writer
lock held by another Codex client produces a rejection; this integration does not
take over the Codex App or IDE.

The SDK adapter resumes the native thread before commands that need it. A native
acceptance starts asynchronous work; review and goal turns use normal runtime
notifications for session status, output and interruptions. Unsupported native
methods return an error. Planning mode uses the experimental native
`thread/settings/update` interface; its request and acknowledgement were checked
against the bundled Codex 0.144.4 schema without changing dependency versions.

## DeepSeek Harness

The bridge lists commands from the authoritative DSH command registry for the
session Agent, then passes the exact submitted line to the registry's parser and
handler. Installed plugins determine the inventory. Optional commands are not
hardcoded into AA, and native validation and result text remain authoritative.

The bridge emits a new catalog revision when the registry changes. The connector,
server and Web client preserve it so the menu refreshes without reconnecting.
The bridge also checks the session source and Agent identity before execution.

The DSH bridge host and Python connector must both include this command support.
An older bridge that does not advertise it reports commands as unavailable with
an upgrade reason. Existing published packages are not changed by checking out
this source branch.

## Results and refresh

`result.executionState` distinguishes these outcomes:

- `accepted`: the native runtime accepted asynchronous work. It may still be running.
- `completed`: a synchronous command finished, or the runtime explicitly rejected it.
- `unknown`: a timeout, disconnection or malformed acknowledgement prevents a
  trustworthy conclusion after dispatch. `retryable: false` prevents automatic
  retry; inspect refreshed session state before choosing a further action.

An HTTP 200 response with `ok: false` is a failure. Web shows the native message or
text and keeps the draft after failure or uncertainty. A late response cannot
erase newer input or update a different session. Commands refresh when the menu
reopens, the connection recovers, runtime availability/status changes or the
catalog revision changes.

The shared UI contract is documented in
[connector-to-runtime](../../docs/runtime-protocol/connector-to-runtime.md#commands)
and [Web behavior](../../docs/runtime-protocol/web-behavior.md#commands).

## Headless verification

From `connector/`:

```bash
uv run pytest -q tests/test_codex_commands.py tests/test_codex_sdk_commands.py \
  tests/test_codex_runtime.py tests/test_dsh_commands.py tests/test_runtime_rpc_params.py
```

From `server/`:

```bash
uv run pytest -q tests/test_session_commands_contract.py
```

From `web-next/`:

```bash
corepack yarn test
corepack yarn typecheck
```

The DSH integration test uses the native registry and a compiled host-to-Python
transport fixture. From `dsh-bridge-next/`, after a host build:

```bash
corepack yarn tsx --test tests/integration/runtime-commands.test.ts
```

These checks do not start an interactive development server or prove an installed
desktop client's behavior. Manual smoke testing requires a connected session and
the matching connector/bridge build.
