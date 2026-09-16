import Link from 'next/link'
import type { ReactNode } from 'react'

/**
 * The frame every public page shares: the Clipah name, the way in, and a quiet footer.
 *
 * `signInLink` is turned off on the sign-in page itself, where a second way to the same
 * place would only be noise.
 */
export function PublicFrame({
  children,
  signInLink = true,
}: {
  children: ReactNode
  signInLink?: boolean
}) {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-5">
        <Link href="/" className="flex items-center gap-2 rounded-lg">
          <span
            aria-hidden="true"
            className="flex size-8 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground"
          >
            C
          </span>
          <span className="text-base font-semibold tracking-tight">Clipah</span>
        </Link>
        <nav aria-label="Site" className="flex items-center gap-2">
          <Link
            href="/demo"
            className="rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground"
          >
            Demo
          </Link>
          {signInLink ? (
            <Link
              href="/signin"
              className="rounded-lg border bg-card px-3 py-2 text-sm font-medium shadow-sm hover:bg-secondary"
            >
              Sign in
            </Link>
          ) : null}
        </nav>
      </header>
      <div className="flex flex-1 flex-col">{children}</div>
      <footer className="mx-auto w-full max-w-6xl px-6 py-8 text-xs text-muted-foreground">
        Clipah turns long videos into short clips you choose, edit, and publish.
      </footer>
    </div>
  )
}
