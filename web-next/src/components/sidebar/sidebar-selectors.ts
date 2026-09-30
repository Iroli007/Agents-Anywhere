import type { WorkspaceSessionView } from "@/components/workspace-context"
import type { ProjectView } from "@/features/dashboard/types"
import { filterSessions, type FilterValue } from "@/lib/demo-api"
import { filterProjectSessions, type DeviceAgentFilter } from "./project-identity"
import {
  projectHasVisibleSessions,
  projectSessionMatchesStatus,
  type ProjectSessionStatusFilter,
} from "./project-visibility"

export type { ProjectSessionStatusFilter } from "./project-visibility"

export function sortSidebarSessions(items: WorkspaceSessionView[]): WorkspaceSessionView[] {
  // The caller passes sessions already in the user's manual sidebar order.
  return [...items]
}

export function selectPinnedProjects(
  projects: ProjectView[],
  sessions: WorkspaceSessionView[],
  status: ProjectSessionStatusFilter,
  filter?: DeviceAgentFilter | null,
): ProjectView[] {
  // The caller passes projects already in the user's manual sidebar order.
  return projects.filter((project) => (
    project.pinned && projectHasVisibleSessions(project, sessions, status, filter)
  ))
}

export function selectRegularProjects(
  projects: ProjectView[],
  sessions: WorkspaceSessionView[],
  status: ProjectSessionStatusFilter,
  filter?: DeviceAgentFilter | null,
): ProjectView[] {
  return projects.filter((project) => (
    !project.pinned && projectHasVisibleSessions(project, sessions, status, filter)
  ))
}

export function selectPinnedSessions(
  sessions: WorkspaceSessionView[],
  filter?: DeviceAgentFilter | null,
): WorkspaceSessionView[] {
  return sortSidebarSessions(
    filterProjectSessions(sessions, filter).filter((session) => session.pinned && !session.archived),
  )
}

export function selectRecentSessions(
  sessions: WorkspaceSessionView[],
  filter: FilterValue,
  search: string,
): WorkspaceSessionView[] {
  return sortSidebarSessions(
    filterSessions(
      sessions.filter((session) => !session.projectId),
      filter,
      search,
    ).filter((session) => session.archived || !session.pinned) as WorkspaceSessionView[],
  )
}

export function selectAllSessions(
  sessions: WorkspaceSessionView[],
  filter: FilterValue,
  search: string,
): WorkspaceSessionView[] {
  return sortSidebarSessions(
    filterSessions(sessions, filter, search).filter((session) => session.archived || !session.pinned),
  )
}

export function selectProjectSessions(
  sessions: WorkspaceSessionView[],
  status: ProjectSessionStatusFilter = "active",
  filter?: DeviceAgentFilter | null,
): WorkspaceSessionView[] {
  return filterProjectSessions(sessions, filter)
    .filter((session) => projectSessionMatchesStatus(session, status))
}

export function groupSessionsByProject(sessions: WorkspaceSessionView[]): Record<string, WorkspaceSessionView[]> {
  const groups: Record<string, WorkspaceSessionView[]> = Object.create(null)
  for (const session of sessions) {
    if (session.projectId) (groups[session.projectId] ??= []).push(session)
  }
  return groups
}
