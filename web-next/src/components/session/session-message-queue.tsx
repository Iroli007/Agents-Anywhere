"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { editQueuedMessage, removeQueuedMessage, retryQueuedMessage, type QueuedMessage } from "@/components/session/message-queue"

export function SessionMessageQueue({ sessionId, messages, canSendNow, onSendNow }: {
  sessionId: string
  messages: QueuedMessage[]
  canSendNow: boolean
  onSendNow: (id: string) => void
}) {
  const t = useTranslations("dashboard.session")
  if (!messages.length) return null
  return (
    <div className="mx-4 max-h-60 overflow-y-auto rounded-lg border bg-background p-2 text-xs" aria-live="polite">
      <p className="mb-1 text-muted-foreground">{t("queueHint")}</p>
      {messages.map((message, index) => (
        <QueuedMessageRow key={message.id} sessionId={sessionId} message={message} index={index} canSendNow={canSendNow} onSendNow={onSendNow} />
      ))}
    </div>
  )
}

function QueuedMessageRow({ sessionId, message, index, canSendNow, onSendNow }: {
  sessionId: string
  message: QueuedMessage
  index: number
  canSendNow: boolean
  onSendNow: (id: string) => void
}) {
  const t = useTranslations("dashboard.session")
  const tNew = useTranslations("dashboard.new")
  const [draft, setDraft] = React.useState(message.content)
  const locked = message.status === "sending" || message.status === "interrupting"
  React.useEffect(() => () => { editQueuedMessage(sessionId, message.id, false) }, [sessionId, message.id])
  return (
    <div className="flex items-start gap-2 py-1">
      <span className="shrink-0 pt-2">{index + 1}. {t(message.status === "failed" ? "queueFailed" : message.status === "sending" ? "queueSending" : message.status === "interrupting" ? "queueInterrupting" : "queued")}</span>
      {message.editing ? (
        <>
          <label className="min-w-0 flex-1 space-y-1">
            <span className="text-muted-foreground">{t("queueEdit")}</span>
            <Textarea autoFocus aria-label={t("queueEdit")} value={draft} onChange={event => setDraft(event.currentTarget.value)} className="min-h-16 text-sm" />
          </label>
          <div className="flex shrink-0 flex-col gap-1">
            <Button size="sm" variant="ghost" disabled={!draft.trim() && message.attachments.length === 0} onClick={() => editQueuedMessage(sessionId, message.id, false, draft)}>{t("queueSave")}</Button>
            <Button size="sm" variant="ghost" onClick={() => editQueuedMessage(sessionId, message.id, false)}>{t("queueCancel")}</Button>
          </div>
        </>
      ) : (
        <>
          <button type="button" disabled={locked} aria-label={t("queueEdit")} className="min-w-0 flex-1 truncate rounded py-2 text-left hover:bg-muted focus-visible:outline focus-visible:outline-ring" title={message.content} onClick={() => { setDraft(message.content); editQueuedMessage(sessionId, message.id, true) }}>
            {message.content || tNew("attachmentOnlyPrompt")}
            {message.attachments.length > 0 ? ` (${message.attachments.map(file => file.name).join(", ")})` : ""}
          </button>
          <div className="flex shrink-0 flex-wrap justify-end gap-1">
            {message.status === "failed" ? <Button size="sm" variant="ghost" onClick={() => retryQueuedMessage(sessionId, message.id)}>{t("queueRetry")}</Button> : null}
            <Button size="sm" variant="ghost" disabled={locked || !canSendNow} onClick={() => onSendNow(message.id)}>{t("queueSendNow")}</Button>
            <Button size="sm" variant="ghost" disabled={locked} onClick={() => removeQueuedMessage(sessionId, message.id)}>{t("queueDelete")}</Button>
          </div>
        </>
      )}
    </div>
  )
}
