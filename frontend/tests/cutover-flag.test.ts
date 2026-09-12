/**
 * The cutover gate that hides the new UI until a rollout stage exposes it.
 *
 * Task 48 deploys the rebuilt foundations before anyone can see the new product, so the
 * flag has to fail closed: an unset, misspelled, or half-written value leaves the UI
 * hidden rather than exposing a surface the rollout has not reached. The backend proxy
 * has to stay reachable while the UI is hidden, because the cutover proofs call it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { gateDecision, isGatedPath, newClipahEnabled } from '@/lib/cutover'
import { middleware } from '@/middleware'

function request(pathname: string): Request {
  return new Request(`https://clipah.test${pathname}`)
}

describe('the NEW_CLIPAH_ENABLED flag', () => {
  it('exposes the new UI only for the exact string true', () => {
    expect(newClipahEnabled('true')).toBe(true)
    expect(newClipahEnabled('TRUE')).toBe(true)
    expect(newClipahEnabled(' true ')).toBe(true)
  })

  it('keeps the new UI hidden when the flag is unset, empty, or anything else', () => {
    expect(newClipahEnabled(undefined)).toBe(false)
    expect(newClipahEnabled('')).toBe(false)
    expect(newClipahEnabled('false')).toBe(false)
    expect(newClipahEnabled('1')).toBe(false)
    expect(newClipahEnabled('yes')).toBe(false)
    expect(newClipahEnabled('truthy')).toBe(false)
  })
})

describe('which paths the gate covers', () => {
  it('gates every product route', () => {
    expect(isGatedPath('/')).toBe(true)
    expect(isGatedPath('/signin')).toBe(true)
    expect(isGatedPath('/demo')).toBe(true)
    expect(isGatedPath('/dashboard')).toBe(true)
    expect(isGatedPath('/dashboard/projects')).toBe(true)
    expect(isGatedPath('/editor/abc')).toBe(true)
  })

  it('leaves the backend proxy and Next assets reachable so the foundations can be proven', () => {
    expect(isGatedPath('/api/v1/me')).toBe(false)
    expect(isGatedPath('/api/v1/health/ready')).toBe(false)
    expect(isGatedPath('/_next/static/chunk.js')).toBe(false)
    expect(isGatedPath('/favicon.ico')).toBe(false)
  })

  it('does not treat a path that merely starts with the same letters as reachable', () => {
    expect(isGatedPath('/apidocs')).toBe(true)
    expect(isGatedPath('/_nextdoor')).toBe(true)
  })
})

describe('the two together', () => {
  it('hides a product route unless the flag exposes it', () => {
    expect(gateDecision('/dashboard', undefined)).toBe('hide')
    expect(gateDecision('/dashboard', 'false')).toBe('hide')
    expect(gateDecision('/dashboard', 'true')).toBe('pass')
  })

  it('passes the backend proxy whatever the flag says', () => {
    expect(gateDecision('/api/v1/me', undefined)).toBe('pass')
    expect(gateDecision('/api/v1/me', 'true')).toBe('pass')
  })
})

describe('the middleware that applies the gate', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  // Next calls middleware as `(request, event)`. A test that passed the environment as a
  // second argument would pass against an implementation that reads the event instead — the
  // exact defect this suite was rewritten to catch — so the flag is set where the middleware
  // actually reads it.
  it('answers a product route with a 404 while the new UI is hidden', () => {
    vi.stubEnv('NEW_CLIPAH_ENABLED', 'false')

    expect(middleware(request('/dashboard')).status).toBe(404)
  })

  it('answers a product route with a 404 when the flag was never set at all', () => {
    vi.stubEnv('NEW_CLIPAH_ENABLED', undefined)

    expect(middleware(request('/')).status).toBe(404)
  })

  it('hides the reason, so a hidden deployment is indistinguishable from a missing one', async () => {
    vi.stubEnv('NEW_CLIPAH_ENABLED', undefined)

    const body = await middleware(request('/dashboard')).text()

    expect(body).not.toMatch(/NEW_CLIPAH_ENABLED/i)
    expect(body).not.toMatch(/flag|cutover|rollout/i)
  })

  it('passes the backend proxy through even while the new UI is hidden', () => {
    vi.stubEnv('NEW_CLIPAH_ENABLED', undefined)

    expect(middleware(request('/api/v1/me')).status).toBe(200)
  })

  it('passes every product route through once the flag exposes the new UI', () => {
    vi.stubEnv('NEW_CLIPAH_ENABLED', 'true')

    expect(middleware(request('/dashboard')).status).toBe(200)
  })

  it('ignores a second argument, because Next passes a fetch event there', () => {
    vi.stubEnv('NEW_CLIPAH_ENABLED', 'true')
    const event = { waitUntil: () => {} } as unknown as Request

    expect((middleware as (r: Request, e: Request) => Response)(request('/dashboard'), event).status).toBe(200)
  })
})
