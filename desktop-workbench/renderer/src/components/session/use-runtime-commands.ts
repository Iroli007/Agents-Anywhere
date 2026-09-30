"use client"

import * as React from "react"
import { dashboardApi } from "@/features/dashboard/api"
import type { RuntimeCommand, RuntimeStatusValue } from "@/features/dashboard/types"

export function createRecoveredSubscriptionTracker(onReconnectRecovered: () => void) {
  let initialConnection: number | null = null
  let lastRecoveredConnection: number | null = null
  return {
    observed(connection: number) {
      if (initialConnection === null) initialConnection = connection
    },
    recovered(connection: number) {
      if (lastRecoveredConnection === connection) return
      lastRecoveredConnection = connection
      if (initialConnection !== null && connection !== initialConnection) onReconnectRecovered()
    },
  }
}

export function useRuntimeCommands({ token, sessionId, open, available, catalogRevision, runtimeStatus, recoveryGeneration = 0 }: {
  token: string
  sessionId: string | null
  open: boolean
  available: boolean
  catalogRevision: string
  runtimeStatus?: RuntimeStatusValue
  recoveryGeneration?: number
}): {commands: RuntimeCommand[]; loading: boolean; error: boolean} {
  // A context identity hides previous-session results during render, before an
  // effect can clear them. Returning to a session always creates a fresh visit.
  const context = React.useMemo(() => ({}), [token, sessionId, open, available, catalogRevision, runtimeStatus, recoveryGeneration])
  const active = open && Boolean(sessionId) && available
  const [catalog, setCatalog] = React.useState<{
    context: object | null; commands: RuntimeCommand[]; loading: boolean; error: boolean
  }>({ context: null, commands: [], loading: false, error: false })
  React.useEffect(() => {
    if (!active || !sessionId) return
    let cancelled = false
    setCatalog({ context, commands: [], loading: true, error: false })
    const timer = window.setTimeout(() => {
      void dashboardApi.getSessionCommands(token, sessionId, { limit: 1000 }).then(response => {
        if (!cancelled) setCatalog({ context, commands: response.commands, loading: false, error: false })
      }).catch(() => {
        if (!cancelled) setCatalog({ context, commands: [], loading: false, error: true })
      })
    }, 120)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [token, sessionId, active, context])
  return catalog.context === context && active
    ? catalog
    : { commands: [], loading: active, error: false }
}
