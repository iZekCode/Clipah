import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import type { BrowserContext } from '@playwright/test'

const run = promisify(execFile)
const BACKEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../../backend')

/** One member the backend seeded, with the cookies a browser has to carry to be them. */
export interface SeededMember {
  userId: string
  workspaceId: string
  sessionCookieName: string
  sessionToken: string
  csrfCookieName: string
  csrfToken: string
}

/**
 * Create one signed-in member directly in the database.
 *
 * A real login would need Google, so the backend exposes a development-only helper that
 * mints the same Session a login would have minted. It refuses to run in production.
 */
export async function seedMember(options: {
  email: string
  displayName: string
  workspaceName: string
}): Promise<SeededMember> {
  const { stdout } = await run(
    'uv',
    [
      'run',
      'python',
      '-m',
      'clipah.dev.seed',
      '--email',
      options.email,
      '--display-name',
      options.displayName,
      '--workspace-name',
      options.workspaceName,
    ],
    { cwd: BACKEND_ROOT },
  )
  return JSON.parse(stdout.trim()) as SeededMember
}

/** Put a seeded member's cookies into one browser context, first-party to the site. */
export async function signIn(
  context: BrowserContext,
  member: SeededMember,
  baseURL: string,
): Promise<void> {
  const { hostname } = new URL(baseURL)
  await context.addCookies([
    {
      name: member.sessionCookieName,
      value: member.sessionToken,
      domain: hostname,
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    },
    {
      name: member.csrfCookieName,
      value: member.csrfToken,
      domain: hostname,
      path: '/',
      sameSite: 'Lax',
    },
  ])
}

/** A unique address, so one run never collides with a member an earlier run seeded. */
export function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 100_000)}@example.com`
}
