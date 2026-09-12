/**
 * The cutover gate.
 *
 * Task 48 deploys the rebuilt database, storage, and job foundations before the new UI is
 * shown to anyone, so the two halves of the cutover can be staged independently. The rule
 * for that lives here, away from any request object, so it can be read and tested without
 * a server.
 */

/**
 * Prefixes that stay reachable while the new UI is hidden.
 *
 * `/api` stays reachable because the backend foundations are deployed first and every
 * cutover proof calls them directly. Next's own assets and the favicon stay reachable
 * because refusing them would break the response the gate itself returns.
 */
const ALWAYS_REACHABLE = ['/api/', '/_next/', '/favicon.ico'] as const

/**
 * Decide whether the new product UI is exposed.
 *
 * The flag is opt-in and fails closed. Only the exact word `true`, in any capitalization
 * and with surrounding space, turns the UI on, so an unset, empty, or half-written value
 * leaves the deployment hidden rather than exposing a surface the rollout has not reached.
 */
export function newClipahEnabled(raw: string | undefined): boolean {
  return raw?.trim().toLowerCase() === 'true'
}

/** Decide whether a path is hidden while the flag is off. */
export function isGatedPath(pathname: string): boolean {
  return !ALWAYS_REACHABLE.some(
    (prefix) => pathname === prefix.replace(/\/$/, '') || pathname.startsWith(prefix),
  )
}

/**
 * The whole gate in one call: what this path and this flag value mean together.
 *
 * The middleware is a thin wrapper around this, so the decision can be tested without a
 * request object and without the fetch event Next passes as a second argument.
 */
export function gateDecision(pathname: string, raw: string | undefined): 'pass' | 'hide' {
  return newClipahEnabled(raw) || !isGatedPath(pathname) ? 'pass' : 'hide'
}
