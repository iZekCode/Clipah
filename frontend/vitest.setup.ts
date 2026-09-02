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
