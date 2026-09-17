import assert from "node:assert/strict"
import test from "node:test"
import { enqueueMessage, readMessageQueue, drainMessageQueue, removeQueuedMessage, retryQueuedMessage, editQueuedMessage, sendQueuedMessageNow } from "../src/components/session/message-queue.ts"

const storage = new Map()
globalThis.sessionStorage = { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
const message = id => ({ id, content: id, attachments: [], selections: {}, status: "queued" })

test("FIFO dispatch is exclusive, preserves concurrent additions, and cannot cancel in-flight messages", async () => {
  enqueueMessage("fifo", message("a"))
  enqueueMessage("fifo", message("b"))
  let complete
  const sent = []
  const first = drainMessageQueue("fifo", async item => { sent.push(item.id); return new Promise(resolve => { complete = resolve }) })
  await drainMessageQueue("fifo", async () => { assert.fail("duplicate dispatch") })
  removeQueuedMessage("fifo", "a")
  enqueueMessage("fifo", message("c"))
  assert.deepEqual(readMessageQueue("fifo").map(item => item.id), ["a", "b", "c"])
  complete(true)
  await first
  assert.deepEqual(readMessageQueue("fifo").map(item => item.id), ["b", "c"])
  await drainMessageQueue("fifo", async item => { sent.push(item.id); return true })
  assert.deepEqual(sent, ["a", "b"])
})

test("failure pauses the queue until explicit retry or cancellation", async () => {
  enqueueMessage("failure", message("a"))
  enqueueMessage("failure", message("b"))
  await drainMessageQueue("failure", async () => false)
  await drainMessageQueue("failure", async () => { assert.fail("must not skip failed head") })
  assert.equal(readMessageQueue("failure")[0].status, "failed")
  retryQueuedMessage("failure", "a")
  await drainMessageQueue("failure", async item => { assert.equal(item.id, "a"); return false })
  removeQueuedMessage("failure", "a")
  await drainMessageQueue("failure", async item => { assert.equal(item.id, "b"); return true })
  assert.deepEqual(readMessageQueue("failure"), [])
})

test("reload restores ordered messages but does not automatically replay an uncertain in-flight request", () => {
  storage.set("aa:message-queue:reload", JSON.stringify([{ ...message("a"), status: "sending" }, message("b")]))
  assert.deepEqual(readMessageQueue("reload").map(item => item.status), ["failed", "queued"])
  assert.deepEqual(readMessageQueue("another-session"), [])
})

test("a late send result only changes the original session queue", async () => {
  enqueueMessage("old", message("a"))
  enqueueMessage("new", message("b"))
  await drainMessageQueue("old", async () => true)
  assert.deepEqual(readMessageQueue("old"), [])
  assert.equal(readMessageQueue("new")[0].id, "b")
})


test("editing pauses dispatch; save changes only text and cancel retains original", async () => {
  enqueueMessage("edit", { ...message("a"), attachments: [{ name: "file" }], selections: { model: "model" } })
  enqueueMessage("edit", message("b"))
  editQueuedMessage("edit", "a", true)
  await drainMessageQueue("edit", async () => assert.fail("editing message must not dispatch"))
  editQueuedMessage("edit", "a", false)
  assert.equal(readMessageQueue("edit")[0].content, "a")
  editQueuedMessage("edit", "a", true)
  editQueuedMessage("edit", "a", false, "updated")
  const [saved, next] = readMessageQueue("edit")
  assert.equal(saved.content, "updated")
  assert.equal(saved.attachments[0].name, "file")
  assert.equal(saved.selections.model, "model")
  assert.equal(next.id, "b")
  await drainMessageQueue("edit", async item => { assert.equal(item.content, "updated"); return true })
})

test("send now interrupts once, blocks dispatch until acknowledgment, and prioritizes the selected item", async () => {
  enqueueMessage("now", message("a"))
  enqueueMessage("now", message("b"))
  let finish
  const request = sendQueuedMessageNow("now", "b", () => new Promise(resolve => { finish = resolve }))
  await sendQueuedMessageNow("now", "a", async () => assert.fail("duplicate interrupt"))
  await drainMessageQueue("now", async () => assert.fail("cannot dispatch before interrupt completes"))
  removeQueuedMessage("now", "b")
  editQueuedMessage("now", "b", true, "wrong")
  enqueueMessage("now", message("c"))
  finish(true)
  await request
  assert.deepEqual(readMessageQueue("now").map(item => item.id), ["b", "a", "c"])
  await drainMessageQueue("now", async item => { assert.equal(item.content, "b"); return true })
  assert.deepEqual(readMessageQueue("now").map(item => item.id), ["a", "c"])
})

test("failed interruption retains the message and does not dispatch it", async () => {
  enqueueMessage("interrupt-failed", message("a"))
  await sendQueuedMessageNow("interrupt-failed", "a", async () => false)
  assert.equal(readMessageQueue("interrupt-failed")[0].status, "failed")
  await drainMessageQueue("interrupt-failed", async () => assert.fail("failed interrupt must pause"))
  removeQueuedMessage("interrupt-failed", "a")
  assert.deepEqual(readMessageQueue("interrupt-failed"), [])
})
