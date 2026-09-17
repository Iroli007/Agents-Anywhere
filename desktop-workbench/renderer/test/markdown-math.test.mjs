import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { JSDOM } from "jsdom"
import { registerSource } from "./helpers/onboarding-source.mjs"

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "https://fixture.example/", pretendToBeVisual: true })
for (const name of ["window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLButtonElement", "Element", "Node", "NodeFilter", "Event", "CustomEvent", "MutationObserver", "getComputedStyle", "requestAnimationFrame", "cancelAnimationFrame"]) {
  Object.defineProperty(globalThis, name, { configurable: true, value: dom.window[name] })
}
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const { createElement: h, act } = await import("react")
const { createRoot } = await import("react-dom/client")
const { NextIntlClientProvider } = await import("next-intl")
const hooks = registerSource()
const { MarkdownText } = await import("../src/components/markdown-text.tsx")
hooks.deregister()

const messages = JSON.parse(readFileSync(new URL("../messages/en.json", import.meta.url), "utf8"))

async function render(t, text) {
  const container = document.createElement("div")
  document.body.append(container)
  const root = createRoot(container)
  await act(async () => root.render(
    h(NextIntlClientProvider, { locale: "en", messages, timeZone: "UTC" }, h(MarkdownText, { text })),
  ))
  t.after(async () => { await act(async () => root.unmount()); container.remove() })
  return container
}

test("inline math renders as KaTeX instead of raw dollar text", async (t) => {
  const container = await render(t, "Mass and energy: $E=mc^2$.")

  assert.ok(container.querySelector(".katex"), `expected KaTeX markup, got: ${container.innerHTML}`)
  assert.ok(container.querySelector(".katex .katex-mathml"), `expected KaTeX mathml, got: ${container.innerHTML}`)
  assert.equal(container.textContent.includes("$E=mc^2$"), false)
  assert.equal(container.querySelector("[node]"), null, `KaTeX spans must not leak the mdast node prop: ${container.innerHTML}`)
})

test("display math renders as a KaTeX display block", async (t) => {
  const container = await render(t, "$$\nE = mc^2\n$$")

  assert.ok(container.querySelector(".katex-display"), `expected display math, got: ${container.innerHTML}`)
})

test("math delimiters inside fenced code stay literal", async (t) => {
  const container = await render(t, "```bash\necho \"$E=mc^2$\"\n```")

  assert.equal(container.querySelector(".katex"), null, `code must not be typeset: ${container.innerHTML}`)
  assert.ok(container.textContent.includes("$E=mc^2$"), `expected literal math source, got: ${container.textContent}`)
})

test("malformed LaTeX renders without throwing", async (t) => {
  const container = await render(t, "broken: $\\frac{1}{$")

  assert.match(container.textContent, /\\frac\{1\}\{/, `expected the raw source to survive, got: ${container.textContent}`)
  assert.doesNotMatch(container.textContent, /\$/, `expected the delimiters to be consumed, got: ${container.textContent}`)
})

for (const text of [
  String.raw`$$\sum_{n=1}^{\infty}\frac{1}{n^2}$$`,
  String.raw`Before $$\sum_{n=1}^{\infty}\frac{1}{n^2}$$ after $$x^2$$.`,
  String.raw`> $$\sum_{n=1}^{\infty}\frac{1}{n^2}$$`,
]) {
  test(`same-line double dollars use display layout: ${text}`, async (t) => {
    const container = await render(t, text)
    assert.equal(container.querySelectorAll('.katex-display').length, text.includes('after') ? 2 : 1)
    assert.ok(container.querySelector('.katex-display .op-limits'))
    assert.equal(container.querySelector('.katex-error'), null)
  })
}

test('chemistry commands render with mhchem', async (t) => {
  const container = await render(t, String.raw`$\ce{2H2 + O2 -> 2H2O}$`)
  assert.ok(container.querySelector('.katex'))
  assert.ok(!container.querySelector('.katex-error, [style*="--destructive"], [style*="#cc0000"], [style*="rgb(204, 0, 0)"]'))
  assert.ok(container.querySelector('msub'))
})

test('invalid and unsupported formulas use the theme error color', async (t) => {
  const container = await render(t, String.raw`$\frac{1}{$ and $\unknowncommand{x}$`)
  const errors = container.querySelectorAll('[style*="color"]')
  assert.ok(errors.length >= 2)
  for (const error of errors) assert.equal(error.style.color, 'var(--destructive)')
})

test('code and escaped dollars remain literal', async (t) => {
  const container = await render(t, '`$$x^2$$` and \\$5\n\n```text\n$$x^2$$\n```')
  assert.equal(container.querySelector('.katex'), null)
  assert.ok(container.textContent.includes('$$x^2$$'))
  assert.ok(container.textContent.includes('$5'))
})
