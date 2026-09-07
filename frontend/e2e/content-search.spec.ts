import { execFile } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

import { expect, test, type Page } from '@playwright/test'

import { seedMemberWithClip, signIn, uniqueEmail, type SeededMember } from './support/seed'

/**
 * Searching a Workspace's own library in a browser, against a real backend.
 *
 * The parts a component test cannot see live here: the rebuild command actually filling
 * the index from durable rows, Postgres actually stemming the words, and a transcript
 * result actually opening its Project at the timecode it named.
 */

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'
const BACKEND_ROOT = fileURLToPath(new URL('../../backend', import.meta.url))
const run = promisify(execFile)

/** Rebuild one Workspace's library from its durable rows, as an operator would. */
async function rebuildIndex(member: SeededMember): Promise<void> {
  await run(
    'uv',
    [
      'run',
      'python',
      '-m',
      'clipah.search.indexer',
      '--workspace',
      member.workspaceId,
      '--actor',
      member.userId,
    ],
    { cwd: BACKEND_ROOT },
  )
}

/** Sign one seeded member in and open a page as them. */
async function open(page: Page, member: SeededMember, path: string): Promise<void> {
  await signIn(page.context(), member, SITE)
  await page.goto(path)
}

/** Ask the library one question and wait for it to answer. */
async function ask(page: Page, question: string): Promise<void> {
  await page.getByRole('searchbox', { name: /search the library/i }).fill(question)
}

test('a member finds a past moment by a word that was spoken in it', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('library-search'),
    displayName: 'Searching Member',
    workspaceName: 'Searching Workspace',
    projectName: 'Searchable Session',
  })
  await rebuildIndex(member)

  await open(page, member, '/dashboard/search')
  await ask(page, 'analysis')

  const results = page.getByRole('list', { name: /search results/i })
  await expect(results.getByText('Searchable Session').first()).toBeVisible()
})

test('a Project is found by a name the member half remembers', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('library-typo'),
    displayName: 'Searching Member',
    workspaceName: 'Searching Workspace',
    projectName: 'Aktivasi Pengguna',
  })
  await rebuildIndex(member)

  await open(page, member, '/dashboard/search')
  await ask(page, 'aktivsi')

  await expect(page.getByRole('link', { name: 'Aktivasi Pengguna' })).toBeVisible()
})

test('a transcript result opens its Project at the timecode it named', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('library-timecode'),
    displayName: 'Searching Member',
    workspaceName: 'Searching Workspace',
    projectName: 'Timecoded Session',
  })
  await rebuildIndex(member)

  await open(page, member, '/dashboard/search')
  await ask(page, 'analysis')
  await page.getByRole('list', { name: /search results/i }).getByRole('link').first().click()

  await expect(page).toHaveURL(/\/dashboard\/projects\/[0-9a-f-]+\?t=\d+/)
  await expect(page.getByText(/the moment you searched for/i)).toBeVisible()
})

test('one Workspace never finds another Workspace’s words', async ({ page }) => {
  const mine = await seedMemberWithClip({
    email: uniqueEmail('library-mine'),
    displayName: 'First Member',
    workspaceName: 'First Workspace',
    projectName: 'Mine Only',
  })
  const theirs = await seedMemberWithClip({
    email: uniqueEmail('library-theirs'),
    displayName: 'Second Member',
    workspaceName: 'Second Workspace',
    projectName: 'Rahasia Dagang',
  })
  await rebuildIndex(mine)
  await rebuildIndex(theirs)

  await open(page, mine, '/dashboard/search')
  await ask(page, 'Rahasia')

  await expect(page.getByText(/nothing in this workspace matches/i)).toBeVisible()
})
