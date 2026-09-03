import '@testing-library/jest-dom/vitest'

// jsdom implements no layout, so it ships no ResizeObserver. Radix primitives observe
// their own elements on mount; a no-op observer is enough for behavioural tests.
if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver
}

// jsdom implements no media pipeline, so the editor's preview would log an unimplemented
// `play`/`pause` on every render. The engine port is what tests assert against; this only
// keeps the real one from shouting about a browser capability jsdom never had.
Object.defineProperty(window.HTMLMediaElement.prototype, 'play', {
  configurable: true,
  value: () => Promise.resolve(),
})
Object.defineProperty(window.HTMLMediaElement.prototype, 'pause', {
  configurable: true,
  value: () => undefined,
})
