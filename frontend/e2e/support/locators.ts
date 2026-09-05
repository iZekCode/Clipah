import type { Locator, Page } from '@playwright/test'

/**
 * Live-region locators that ignore the one Next.js injects.
 *
 * Next renders `<div role="alert" id="__next-route-announcer__">` outside the page's own
 * markup to announce navigations, so a bare `getByRole('alert')` matches two elements and
 * Playwright refuses it in strict mode. Scoping to `main` is not an answer either: a page
 * that is nothing but an error renders no landmark at all.
 */
const ROUTE_ANNOUNCER = '#__next-route-announcer__'

/** The page's own alert, whatever landmark it happens to sit in. */
export function alertOf(page: Page): Locator {
  return page.locator(`[role="alert"]:not(${ROUTE_ANNOUNCER})`)
}

/** The page's own status messages, newest last. */
export function statusOf(page: Page): Locator {
  return page.locator(`[role="status"]:not(${ROUTE_ANNOUNCER})`)
}
