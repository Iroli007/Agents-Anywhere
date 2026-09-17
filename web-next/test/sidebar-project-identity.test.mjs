import assert from "node:assert/strict"
import test from "node:test"
import { registerSource } from "./helpers/onboarding-source.mjs"

const hooks = registerSource()
const {
  filterProjectSessions,
  hasDuplicateProjectName,
  isDeviceAgentFilterActive,
  projectIdentityLabel,
  projectMatchesDeviceAgentFilter,
  resolveProjectIdentity,
  sessionIdentityLabel,
  shouldShowProjectIdentity,
  workspaceHasMultipleAgents,
  workspaceHasMultipleDevices,
} = await import("../src/components/sidebar/project-identity.ts")
const { projectHasVisibleSessions } = await import("../src/components/sidebar/project-visibility.ts")
const {
  selectPinnedProjects,
  selectProjectSessions,
  selectRegularProjects,
} = await import("../src/components/sidebar/sidebar-selectors.ts")
hooks.deregister()

const all = { connectorId: "all", runtime: "all" }
const connector = (id, name, deviceOs = "macos") => ({ id, name, deviceOs })
const project = (id, overrides = {}) => ({
  id,
  name: id,
  connectorId: "conn-a",
  workspacePath: `/work/${id}`,
  pinned: false,
  pinnedAt: null,
  activeSessionCount: 1,
  lastActivityAt: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  ...overrides,
})
const session = (projectId, runtime, overrides = {}) => ({
  id: `${projectId}-${runtime}-${overrides.connectorId ?? "conn-a"}`,
  projectId,
  connectorId: "conn-a",
  runtime,
  archived: false,
  pinned: false,
  ...overrides,
})
const agent = (runtime, label, sessionCount) => ({ runtime, label, sessionCount })

test("identity resolves the device, workspace and deduplicated Agents with counts", () => {
  const sessions = [
    session("p1", "codex", { runtimeTypeDisplayName: "Codex" }),
    session("p1", "codex"),
    session("p1", "claude", { runtimeTypeDisplayName: "Claude Code" }),
    session("p1", "dsh", { runtimeTypeDisplayName: null }),
    session("p2", "claude"),
  ]
  const identity = resolveProjectIdentity(project("p1"), sessions, [connector("conn-a", "MacBook Pro")])

  assert.equal(identity.deviceName, "MacBook Pro")
  assert.equal(identity.deviceOs, "macos")
  assert.equal(identity.workspacePath, "/work/p1")
  assert.deepEqual(identity.agents, [
    agent("codex", "Codex", 2),
    agent("claude", "Claude Code", 1),
    agent("dsh", "DeepSeek Harness", 1),
  ])
})

test("identity falls back to the connector id and to the runtime name", () => {
  const identity = resolveProjectIdentity(project("p1"), [session("p1", "opencode")], [])

  assert.equal(identity.deviceName, "conn-a")
  assert.equal(identity.deviceOs, null)
  assert.deepEqual(identity.agents, [agent("opencode", "OpenCode", 1)])
  assert.deepEqual(resolveProjectIdentity(project("p9"), [], []).agents, [])
})

test("the compact label carries the device, the primary Agent and a +N suffix", () => {
  const base = { deviceName: "MacBook Pro", deviceOs: "macos", workspacePath: "/work/p1" }

  assert.equal(
    projectIdentityLabel({ ...base, agents: [agent("codex", "Codex", 2)] }),
    "MacBook Pro · Codex",
  )
  assert.equal(
    projectIdentityLabel({
      ...base,
      agents: [agent("codex", "Codex", 2), agent("claude", "Claude Code", 1), agent("dsh", "DeepSeek Harness", 1)],
    }),
    "MacBook Pro · Codex +2",
  )
  assert.equal(
    projectIdentityLabel({ ...base, deviceName: "", agents: [agent("codex", "Codex", 1)] }),
    "Codex",
  )
  assert.equal(projectIdentityLabel({ ...base, deviceName: "  ", agents: [] }), null)
})

test("the device/Agent gate matches device only, Agent only, both and neither", () => {
  const sessions = [session("p1", "codex"), session("p1", "claude", { connectorId: "conn-b" })]
  const target = project("p1")

  assert.equal(isDeviceAgentFilterActive(all), false)
  assert.equal(isDeviceAgentFilterActive({ connectorId: "all", runtime: "codex" }), true)
  assert.equal(isDeviceAgentFilterActive({ connectorId: "conn-a", runtime: "all" }), true)

  assert.equal(projectMatchesDeviceAgentFilter(target, sessions, all), true)
  assert.equal(projectMatchesDeviceAgentFilter(target, sessions), true)
  assert.equal(projectMatchesDeviceAgentFilter(target, sessions, { connectorId: "conn-a", runtime: "all" }), true)
  assert.equal(projectMatchesDeviceAgentFilter(target, sessions, { connectorId: "conn-b", runtime: "all" }), false)
  assert.equal(projectMatchesDeviceAgentFilter(target, sessions, { connectorId: "all", runtime: "claude" }), true)
  assert.equal(projectMatchesDeviceAgentFilter(target, sessions, { connectorId: "all", runtime: "gemini" }), false)
  assert.equal(projectMatchesDeviceAgentFilter(target, sessions, { connectorId: "conn-a", runtime: "codex" }), true)
  assert.equal(projectMatchesDeviceAgentFilter(target, sessions, { connectorId: "conn-b", runtime: "codex" }), false)
})

test("an empty manual project survives \"all\" but not an active device/Agent filter", () => {
  const manual = project("p1", {
    manuallyCreated: true,
    activeSessionCount: 0,
    sidebarSessionCounts: { active: 0, archived: 0 },
  })

  assert.equal(projectHasVisibleSessions(manual, [], "active", all), true)
  assert.equal(projectHasVisibleSessions(manual, [], "active"), true)
  assert.equal(projectHasVisibleSessions(manual, [], "active", { connectorId: "all", runtime: "codex" }), false)
  assert.equal(projectHasVisibleSessions(manual, [], "active", { connectorId: "conn-b", runtime: "all" }), false)
})

test("a project whose only matching session is archived follows the status filter", () => {
  const target = project("p1")
  const sessions = [session("p1", "codex", { archived: true }), session("p1", "claude")]
  const agentFilter = { connectorId: "all", runtime: "codex" }

  assert.equal(projectHasVisibleSessions(target, sessions, "active", agentFilter), false)
  assert.equal(projectHasVisibleSessions(target, sessions, "archived", agentFilter), true)
  assert.equal(projectHasVisibleSessions(target, sessions, "all", agentFilter), true)

  assert.deepEqual(selectRegularProjects([target], sessions, "active", agentFilter), [])
  assert.deepEqual(selectRegularProjects([target], sessions, "archived", agentFilter).map((item) => item.id), ["p1"])
  assert.deepEqual(selectRegularProjects([target], sessions, "active", all).map((item) => item.id), ["p1"])
})

test("an expanded project hides the sessions of other devices and Agents", () => {
  // Callers hand over the sessions already grouped for one project.
  const projectSessions = [
    session("p1", "codex"),
    session("p1", "claude"),
    session("p1", "codex", { connectorId: "conn-b" }),
  ]

  assert.equal(filterProjectSessions(projectSessions, all).length, 3)
  assert.equal(filterProjectSessions(projectSessions).length, 3)
  assert.deepEqual(
    filterProjectSessions(projectSessions, { connectorId: "conn-a", runtime: "codex" })
      .map((item) => item.id),
    ["p1-codex-conn-a"],
  )
  assert.deepEqual(
    selectProjectSessions(projectSessions, "active", { connectorId: "all", runtime: "codex" })
      .map((item) => item.id),
    ["p1-codex-conn-a", "p1-codex-conn-b"],
  )
  assert.deepEqual(
    selectProjectSessions([session("p1", "codex", { archived: true })], "active", all),
    [],
  )
})

test("a same-named project on another device is flagged for an identity hint", () => {
  const local = project("p1", { name: "api", connectorId: "conn-a" })
  const remote = project("p2", { name: "api", connectorId: "conn-b" })
  const unique = project("p3", { name: "web" })
  const projects = [local, remote, unique]

  assert.equal(hasDuplicateProjectName(local, projects), true)
  assert.equal(hasDuplicateProjectName(remote, projects), true)
  assert.equal(hasDuplicateProjectName(unique, projects), false)
  assert.equal(hasDuplicateProjectName(local, [local]), false)

  assert.equal(shouldShowProjectIdentity(local, projects, { multipleDevices: false, multipleAgents: false }), true)
  assert.equal(shouldShowProjectIdentity(unique, projects, { multipleDevices: false, multipleAgents: false }), false)
  assert.equal(shouldShowProjectIdentity(unique, projects, { multipleDevices: true, multipleAgents: false }), true)
  assert.equal(shouldShowProjectIdentity(unique, projects, { multipleDevices: false, multipleAgents: true }), true)
})

test("session rows and workspace flags keep device and Agent identity available", () => {
  const connectors = [connector("conn-a", "MacBook Pro"), connector("conn-b", "Build Box", "linux")]

  assert.equal(
    sessionIdentityLabel(session("p1", "codex", { runtimeTypeDisplayName: "Codex" }), connectors),
    "MacBook Pro · Codex",
  )
  assert.equal(
    sessionIdentityLabel(session("p1", "dsh", { connectorId: "conn-b" }), connectors),
    "Build Box · DeepSeek Harness",
  )
  assert.equal(sessionIdentityLabel(session("p1", "codex"), []), "conn-a · Codex")

  assert.equal(workspaceHasMultipleDevices(connectors), true)
  assert.equal(workspaceHasMultipleDevices([connector("conn-a", "MacBook Pro")]), false)
  assert.equal(workspaceHasMultipleAgents([session("p1", "codex"), session("p2", "codex")]), false)
  assert.equal(workspaceHasMultipleAgents([session("p1", "codex"), session("p2", "claude")]), true)
})

test("identity read from filtered sessions never contradicts the Agent filter", () => {
  const sessions = [session("p1", "codex"), session("p1", "codex"), session("p1", "claude")]
  const filtered = filterProjectSessions(sessions, { connectorId: "all", runtime: "claude" })

  assert.deepEqual(resolveProjectIdentity(project("p1"), filtered, []).agents, [agent("claude", "Claude Code", 1)])
  assert.deepEqual(resolveProjectIdentity(project("p1"), sessions, []).agents, [
    agent("codex", "Codex", 2),
    agent("claude", "Claude Code", 1),
  ])
})

test("the pinned section applies the same device/Agent gate", () => {
  const pinned = project("p1", { pinned: true })
  const sessions = [session("p1", "codex")]

  assert.deepEqual(
    selectPinnedProjects([pinned], sessions, "active", { connectorId: "all", runtime: "codex" })
      .map((item) => item.id),
    ["p1"],
  )
  assert.deepEqual(selectPinnedProjects([pinned], sessions, "active", { connectorId: "all", runtime: "claude" }), [])
  assert.deepEqual(selectPinnedProjects([pinned], [], "active", { connectorId: "all", runtime: "claude" }), [])
})
