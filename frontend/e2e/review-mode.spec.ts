import { expect, test } from '@playwright/test'

import { seedMemberWithClip, signIn, uniqueEmail } from './support/seed'

const SITE = process.env.CLIPAH_E2E_BASE_URL ?? 'http://localhost:3000'

test('a member reviews a moment from the keyboard and opens it in the editor', async ({ page }) => {
  const member = await seedMemberWithClip({
    email: uniqueEmail('review-mode'),
    displayName: 'Reviewing Member',
    workspaceName: 'Reviewing Workspace',
    projectName: 'Reviewed Episode',
  })
  await signIn(page.context(), member, SITE)

  await page.goto(`/dashboard/projects/${member.project.projectId}/review`)
  await expect(
    page.getByRole('region', { name: 'Moment' }).getByRole('heading', { level: 1 }),
  ).toBeVisible()

  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: 'Review shortcuts' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Review shortcuts' })).toBeHidden()

  await page.keyboard.press('e')
  await expect(page).toHaveURL(/\/editor\//)
})
