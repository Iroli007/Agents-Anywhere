import type { AttachedFile } from "@/components/attachment-input"

export type QueuedMessage = {
  id: string
  content: string
  attachments: Omit<AttachedFile, "file" | "preview">[]
  selections: { model?: string; permission?: string }
  status: "queued" | "sending" | "interrupting" | "failed"
  editing?: boolean
}

const queues = new Map<string, QueuedMessage[]>()
const listeners = new Set<() => void>()
const empty: QueuedMessage[] = []
const storageKey = (sessionId: string) => `aa:message-queue:${sessionId}`

export function subscribeMessageQueue(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function readMessageQueue(sessionId: string): QueuedMessage[] {
  const cached = queues.get(sessionId)
  if (cached) return cached
  let messages: QueuedMessage[] = []
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey(sessionId)) ?? "[]") as QueuedMessage[]
    if (Array.isArray(saved)) {
      messages = saved.filter(item => item && typeof item.id === "string" && typeof item.content === "string" && Array.isArray(item.attachments) && item.selections)
        // A request interrupted by reload may already have reached the agent.
        .map(item => ({ ...item, editing: false, status: item.status === "queued" ? "queued" : "failed" }))
    }
  } catch { /* Storage can be disabled by the browser. */ }
  queues.set(sessionId, messages)
  return messages
}

export function emptyMessageQueue() { return empty }

function writeMessageQueue(sessionId: string, messages: QueuedMessage[]) {
  queues.set(sessionId, messages)
  try {
    if (messages.length) sessionStorage.setItem(storageKey(sessionId), JSON.stringify(messages))
    else sessionStorage.removeItem(storageKey(sessionId))
  } catch { /* Keep the queue usable when browser storage is unavailable. */ }
  listeners.forEach(listener => listener())
}

export function enqueueMessage(sessionId: string, message: QueuedMessage) {
  writeMessageQueue(sessionId, [...readMessageQueue(sessionId), message])
}

export function removeQueuedMessage(sessionId: string, id: string) {
  writeMessageQueue(sessionId, readMessageQueue(sessionId).filter(item => item.id !== id || item.status === "sending" || item.status === "interrupting"))
}

export function retryQueuedMessage(sessionId: string, id: string) {
  writeMessageQueue(sessionId, readMessageQueue(sessionId).map(item => item.id === id && item.status === "failed" ? { ...item, status: "queued" } : item))
}

export async function drainMessageQueue(sessionId: string, send: (message: QueuedMessage) => Promise<boolean>) {
  const next = readMessageQueue(sessionId)[0]
  if (!next || next.status !== "queued" || next.editing || readMessageQueue(sessionId).some(item => item.status === "interrupting")) return
  writeMessageQueue(sessionId, readMessageQueue(sessionId).map(item => item.id === next.id ? { ...item, status: "sending" } : item))
  let sent = false
  try {
    sent = await send(next)
  } finally {
    writeMessageQueue(sessionId, sent
      ? readMessageQueue(sessionId).filter(item => item.id !== next.id)
      : readMessageQueue(sessionId).map(item => item.id === next.id ? { ...item, status: "failed" } : item))
  }
}

export function editQueuedMessage(sessionId: string, id: string, editing: boolean, content?: string) {
  writeMessageQueue(sessionId, readMessageQueue(sessionId).map(item => {
    if (item.id !== id || item.status === "sending" || item.status === "interrupting") return item
    if (content !== undefined && !content.trim() && item.attachments.length === 0) return item
    return { ...item, editing, ...(content !== undefined ? { content } : {}) }
  }))
}

export async function sendQueuedMessageNow(sessionId: string, id: string, interrupt: () => Promise<boolean>) {
  const messages = readMessageQueue(sessionId)
  const message = messages.find(item => item.id === id)
  if (!message || message.editing || messages.some(item => item.status === "sending" || item.status === "interrupting")) return
  writeMessageQueue(sessionId, messages.map(item => item.id === id ? { ...item, status: "interrupting" } : item))
  let ready = false
  try {
    ready = await interrupt()
  } finally {
    const remaining = readMessageQueue(sessionId)
    if (ready) {
      writeMessageQueue(sessionId, [{ ...message, status: "queued" }, ...remaining.filter(item => item.id !== id)])
    } else {
      writeMessageQueue(sessionId, remaining.map(item => item.id === id ? { ...item, status: "failed" } : item))
    }
  }
}
