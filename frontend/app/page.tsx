import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'

import { LandingPage } from '@/components/landing-page'
import { SESSION_COOKIE_NAMES } from '@/lib/api/client'

/**
 * The site root: the landing page, unless the visitor already holds a session.
 *
 * The check runs on the server, so a signed-in member never sees the landing page flash
 * past. It only looks for the cookie; a session that has since ended reaches the dashboard's
 * own sign-in gate, which is the right place to say so.
 */
export default async function LandingRoute() {
  const jar = await cookies()
  if (SESSION_COOKIE_NAMES.some((name) => jar.has(name))) {
    redirect('/dashboard')
  }
  return <LandingPage />
}
