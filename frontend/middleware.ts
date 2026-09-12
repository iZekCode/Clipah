/**
 * Hide the new product UI until a rollout stage exposes it.
 *
 * The cutover deploys the rebuilt foundations first and the UI second. Until
 * `NEW_CLIPAH_ENABLED` says otherwise, every product route answers exactly as a route that
 * does not exist: a bare 404 that names no flag and no deployment, so a stage that has not
 * been reached yet is indistinguishable from a Clipah that was never there. The backend
 * proxy keeps answering throughout, because the cutover proofs run against it.
 *
 * Next calls this with `(request, event)`, so the environment cannot be an injected second
 * parameter — an event object would silently shadow it and hide the UI forever. The
 * decision itself lives in `gateDecision`, which takes what it needs and is tested directly.
 */

import { NextResponse } from 'next/server'

import { gateDecision } from '@/lib/cutover'

export function middleware(request: Request): NextResponse {
  const { pathname } = new URL(request.url)

  if (gateDecision(pathname, process.env.NEW_CLIPAH_ENABLED) === 'pass') {
    return NextResponse.next()
  }

  return new NextResponse(null, { status: 404 })
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
