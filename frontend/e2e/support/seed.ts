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

/** One analysed Project the backend staged, and the clip its editor opens on. */
export interface SeededClip {
  projectId: string
  candidateId: string
  sourceAssetId: string
  name: string
}

/** A member who also owns a Project that has finished analysis. */
export interface SeededMemberWithClip extends SeededMember {
  project: SeededClip
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
  teamWorkspace?: boolean
}): Promise<SeededMember> {
  const teamArgs = options.teamWorkspace ? ['--team-workspace'] : []
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
      ...teamArgs,
    ],
    { cwd: BACKEND_ROOT },
  )
  return JSON.parse(stdout.trim()) as SeededMember
}

/**
 * Create one signed-in member who already owns a clip the analysis produced.
 *
 * No API can create a Clip Candidate, so the scenarios that edit, review, or illustrate
 * a real clip cannot set themselves up through the product. The backend stages the rows
 * its pipeline would have written; the media those rows name is never fetched.
 */
export async function seedMemberWithClip(options: {
  email: string
  displayName: string
  workspaceName: string
  projectName: string
  teamWorkspace?: boolean
}): Promise<SeededMemberWithClip> {
  const teamArgs = options.teamWorkspace ? ['--team-workspace'] : []
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
      '--with-clip',
      options.projectName,
      ...teamArgs,
    ],
    { cwd: BACKEND_ROOT },
  )
  return JSON.parse(stdout.trim()) as SeededMemberWithClip
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
