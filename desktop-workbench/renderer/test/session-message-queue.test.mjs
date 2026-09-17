import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { JSDOM } from "jsdom"
import { registerSource } from "./helpers/onboarding-source.mjs"

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "https://fixture.example/", pretendToBeVisual: true })
for (const name of ["window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLTextAreaElement", "HTMLFormElement", "HTMLButtonElement", "Element", "Node", "NodeFilter", "Event", "CustomEvent", "MutationObserver", "getComputedStyle", "requestAnimationFrame", "cancelAnimationFrame"]) {
  Object.defineProperty(globalThis, name, { configurable: true, value: dom.window[name] })
}
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const { createElement: h, act, useState } = await import("react")
const { createRoot } = await import("react-dom/client")
const { NextIntlClientProvider } = await import("next-intl")
const hooks = registerSource()
const { SessionMessageQueue } = await import("../src/components/session/session-message-queue.tsx")
const { enqueueMessage, readMessageQueue, subscribeMessageQueue, emptyMessageQueue } = await import("../src/components/session/message-queue.ts")
hooks.deregister()
const { useSyncExternalStore } = await import("react")
const messages = JSON.parse(readFileSync(new URL("../messages/zh-CN.json", import.meta.url)))

async function renderQueue(t, sessionId) {
  const sends = []
  enqueueMessage(sessionId, { id: "message", content: "原文字", attachments: [], selections: {}, status: "queued" })
  function Fixture() {
    const queue = useSyncExternalStore(subscribeMessageQueue, () => readMessageQueue(sessionId), emptyMessageQueue)
    return h(NextIntlClientProvider, { locale: "zh-CN", messages }, h(SessionMessageQueue, { sessionId, messages: queue, canSendNow: true, onSendNow: id => sends.push(id) }))
  }
  const container = document.createElement("div")
  document.body.append(container)
  const root = createRoot(container)
  await act(async () => root.render(h(Fixture)))
  t.after(async () => { await act(async () => root.unmount()); container.remove() })
  return { container, sends }
}

async function click(container, text) {
  const button = [...container.querySelectorAll("button")].find(button => button.textContent === text)
  assert.ok(button, `Missing button: ${text}`)
  await act(async () => button.click())
}

async function type(container, value) {
  const input = container.querySelector("textarea")
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set.call(input, value)
    input.dispatchEvent(new window.Event("input", { bubbles: true }))
  })
}

test("clicking a queued message edits text with save and cancel; cancel discards and save persists", async t => {
  const { container } = await renderQueue(t, "ui-edit")
  await click(container, "原文字")
  assert.equal(container.querySelector("textarea").getAttribute("aria-label"), "修改文字")
  await type(container, "取消的修改")
  await click(container, "取消")
  assert.equal(readMessageQueue("ui-edit")[0].content, "原文字")
  await click(container, "原文字")
  await type(container, "保存的修改")
  await click(container, "保存")
  assert.equal(container.querySelector("textarea"), null)
  assert.equal(readMessageQueue("ui-edit")[0].content, "保存的修改")
})

test("send now targets the selected message and delete immediately removes it", async t => {
  const { container, sends } = await renderQueue(t, "ui-actions")
  await click(container, "立即发送")
  assert.deepEqual(sends, ["message"])
  await click(container, "删除")
  assert.deepEqual(readMessageQueue("ui-actions"), [])
  assert.equal(container.textContent, "")
})
