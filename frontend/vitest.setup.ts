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

// Node 25 defines its own `localStorage` global, which hides jsdom's and, without a
// `--localstorage-file`, has no methods at all. Put jsdom's Storage back so components and
// spies on `Storage.prototype` see the browser API they were written against.
const jsdomWindow = (globalThis as { jsdom?: { window: Window } }).jsdom?.window
if (jsdomWindow !== undefined && typeof globalThis.localStorage?.clear !== 'function') {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: jsdomWindow.localStorage,
  })
}

// jsdom lays nothing out, so it has no `scrollIntoView`; cmdk calls it to keep the
// highlighted command visible. Scrolling is not what these tests observe.
if (typeof Element.prototype.scrollIntoView !== 'function') {
  Element.prototype.scrollIntoView = function scrollIntoView(): void {}
}

// Floating UI (under every Radix tooltip, popover, and menu) asks each ancestor whether it
// matches `:popover-open` or `:modal`. jsdom's selector engine answers `:modal` by walking
// `:fullscreen` recursively — about seven seconds for one tooltip. jsdom has no top layer
// and no fullscreen, so the honest answer is always "no", given without the walk.
const TOP_LAYER_SELECTORS = new Set([':popover-open', ':modal', ':fullscreen'])
const nativeMatches = Element.prototype.matches
Element.prototype.matches = function matches(this: Element, selectors: string): boolean {
  return TOP_LAYER_SELECTORS.has(selectors) ? false : nativeMatches.call(this, selectors)
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

// `next/font` is a build-time transform; under Vitest a font is just its variable name.
vi.mock('next/font/local', () => ({
  default: (options: { variable?: string }) => ({
    className: 'font',
    variable: options.variable ?? 'font-variable',
    style: { fontFamily: 'sans-serif' },
  }),
}))
