import { beforeEach, describe, expect, test, vi } from 'vitest'

const cookieNames = vi.hoisted(() => ({ present: [] as string[] }))
const redirect = vi.hoisted(() =>
  vi.fn((path: string) => {
    // Next's redirect throws to stop rendering; mirror that so nothing renders after it.
    throw new Error(`NEXT_REDIRECT ${path}`)
  }),
)

vi.mock('next/headers', () => ({
  cookies: async () => ({ has: (name: string) => cookieNames.present.includes(name) }),
}))
vi.mock('next/navigation', () => ({ redirect }))

import LandingRoute from '@/app/page'

beforeEach(() => {
  redirect.mockClear()
  cookieNames.present = []
})

describe('the landing route', () => {
  test('sends a visitor holding a session straight to the dashboard', async () => {
    cookieNames.present = ['clipah_session']

    await expect(LandingRoute()).rejects.toThrow('NEXT_REDIRECT /dashboard')
    expect(redirect).toHaveBeenCalledWith('/dashboard')
  })

  test('recognises the production cookie name too', async () => {
    cookieNames.present = ['__Host-clipah_session']

    await expect(LandingRoute()).rejects.toThrow('NEXT_REDIRECT /dashboard')
  })

  test('shows the landing page to everyone else', async () => {
    cookieNames.present = ['clipah_csrf']

    await expect(LandingRoute()).resolves.toBeTruthy()
    expect(redirect).not.toHaveBeenCalled()
  })
})
