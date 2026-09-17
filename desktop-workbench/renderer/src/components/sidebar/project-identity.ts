// The explicit `.ts` extension keeps this module importable by `node --test`
// without a bundler, like the other pure sidebar modules.
import { runtimeLabel } from "../session/session-utils.ts"
import type { FilterValue } from "@/lib/demo-api"
import type { ProjectView } from "@/features/dashboard/types"

/**
 * The device/Agent half of the sidebar filter. Search and archive status stay
 * with the section that owns them, so projects keep their own status filter.
 */
export type DeviceAgentFilter = Pick<FilterValue, "connectorId" | "runtime">

type FilterableSession = {
  connectorId?: string
  runtime?: string
}

type ProjectSession = FilterableSession & {
  projectId?: string | null
  runtimeTypeDisplayName?: string | null
}

type IdentityConnector = {
  id: string
  name: string
  deviceOs?: string | null
}

export type ProjectIdentityAgent = {
  runtime: string
  label: string
  sessionCount: number
}

export type ProjectIdentity = {
  deviceName: string
  deviceOs: string | null
  agents: ProjectIdentityAgent[]
  workspacePath: string
}

/**
 * Device and Agent identity of the sessions that live in one project.
 * `deviceName` falls back to the connector id so same-named projects on
 * different devices stay distinguishable even before connectors load.
 */
export function resolveProjectIdentity(
  project: Pick<ProjectView, "id" | "connectorId" | "workspacePath">,
  sessions: readonly ProjectSession[],
  connectors: readonly IdentityConnector[],
): ProjectIdentity {
  const connector = connectors.find((item) => item.id === project.connectorId)
  const groups = new Map<string, { displayName: string | null; sessionCount: number }>()
  for (const session of sessions) {
    if (session.projectId !== project.id || !session.runtime) continue
    const group = groups.get(session.runtime) ?? { displayName: null, sessionCount: 0 }
    const displayName = session.runtimeTypeDisplayName?.trim()
    if (displayName && !group.displayName) group.displayName = displayName
    group.sessionCount += 1
    groups.set(session.runtime, group)
  }

  const agents = Array.from(groups, ([runtime, group]) => ({
    runtime,
    label: group.displayName || runtimeLabel(runtime),
    sessionCount: group.sessionCount,
  })).sort((left, right) => (
    right.sessionCount - left.sessionCount
    || left.label.localeCompare(right.label)
    || left.runtime.localeCompare(right.runtime)
  ))

  return {
    deviceName: connector?.name?.trim() || project.connectorId,
    deviceOs: connector?.deviceOs ?? null,
    agents,
    workspacePath: project.workspacePath,
  }
}

/**
 * Compact one-line label for a project row, e.g. `MacBook Pro · Codex`.
 * Extra Agents collapse into a `+N` suffix; `null` means nothing to show.
 */
export function projectIdentityLabel(identity: ProjectIdentity): string | null {
  const parts: string[] = []
  const deviceName = identity.deviceName.trim()
  if (deviceName) parts.push(deviceName)
  const [primary, ...rest] = identity.agents
  if (primary) parts.push(rest.length > 0 ? `${primary.label} +${rest.length}` : primary.label)
  return parts.length > 0 ? parts.join(" · ") : null
}

/** Short `device · agent` label for a session row that is not inside a project. */
export function sessionIdentityLabel(
  session: FilterableSession & { runtimeTypeDisplayName?: string | null },
  connectors: readonly IdentityConnector[],
): string | null {
  const deviceName = (connectors.find((item) => item.id === session.connectorId)?.name
    || session.connectorId
    || "").trim()
  const label = session.runtime
    ? session.runtimeTypeDisplayName?.trim() || runtimeLabel(session.runtime)
    : ""
  const parts = [deviceName, label].filter(Boolean)
  return parts.length > 0 ? parts.join(" · ") : null
}

/** True when the workspace spans more than one paired device. */
export function workspaceHasMultipleDevices(connectors: readonly { id: string }[]): boolean {
  return new Set(connectors.map((connector) => connector.id)).size > 1
}

/** True when the workspace spans more than one Agent runtime. */
export function workspaceHasMultipleAgents(sessions: readonly { runtime?: string }[]): boolean {
  return new Set(sessions.map((session) => session.runtime).filter(Boolean)).size > 1
}

/** True when another project shows the same name, which needs an identity hint. */
export function hasDuplicateProjectName(
  project: Pick<ProjectView, "id" | "name">,
  projects: readonly Pick<ProjectView, "id" | "name">[],
): boolean {
  const name = project.name.trim()
  if (!name) return false
  return projects.some((other) => other.id !== project.id && other.name.trim() === name)
}

/**
 * Project rows only earn a second line when identity is ambiguous: several
 * devices, several Agents, or a name that collides with another project.
 */
export function shouldShowProjectIdentity(
  project: Pick<ProjectView, "id" | "name">,
  projects: readonly Pick<ProjectView, "id" | "name">[],
  options: { multipleDevices: boolean; multipleAgents: boolean },
): boolean {
  return options.multipleDevices || options.multipleAgents || hasDuplicateProjectName(project, projects)
}

/** True when the device/Agent gate is actually narrowing the projects list. */
export function isDeviceAgentFilterActive(filter?: DeviceAgentFilter | null): boolean {
  if (!filter) return false
  return filter.connectorId !== "all" || filter.runtime !== "all"
}

export function sessionMatchesDeviceAgentFilter(
  session: FilterableSession,
  filter?: DeviceAgentFilter | null,
): boolean {
  if (!filter) return true
  if (filter.connectorId !== "all" && session.connectorId !== filter.connectorId) return false
  if (filter.runtime !== "all" && session.runtime !== filter.runtime) return false
  return true
}

/** Sessions of one project that survive the device/Agent gate. */
export function filterProjectSessions<S extends FilterableSession>(
  sessions: readonly S[],
  filter?: DeviceAgentFilter | null,
): S[] {
  return sessions.filter((session) => sessionMatchesDeviceAgentFilter(session, filter))
}

/**
 * Device gate on the project itself plus an Agent gate that needs at least one
 * session running on the selected runtime.
 */
export function projectMatchesDeviceAgentFilter(
  project: Pick<ProjectView, "id" | "connectorId">,
  sessions: readonly ProjectSession[],
  filter?: DeviceAgentFilter | null,
): boolean {
  if (!filter) return true
  if (filter.connectorId !== "all" && project.connectorId !== filter.connectorId) return false
  if (filter.runtime === "all") return true
  return sessions.some((session) => session.projectId === project.id && session.runtime === filter.runtime)
}
