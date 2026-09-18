import Link from 'next/link'
import type { ReactNode } from 'react'

import { Wordmark } from '@/components/shell/wordmark'

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
      <header className="mx-auto flex w-full max-w-studio items-center justify-between px-4 py-5 sm:px-6">
        <Link href="/" aria-label="Clipah home" className="rounded-md">
          <Wordmark />
        </Link>
        <nav aria-label="Site" className="flex items-center gap-2">
          <Link
            href="/demo"
            className="rounded-md px-3 py-2 text-small font-semibold text-muted-foreground transition-colors duration-fast ease-signal hover:text-foreground"
          >
            Demo
          </Link>
          {signInLink ? (
            <Link
              href="/signin"
              className="rounded-md border border-line-strong px-3 py-2 text-small font-semibold transition-colors duration-fast ease-signal hover:border-input"
            >
              Sign in
            </Link>
          ) : null}
        </nav>
      </header>
      <div className="flex flex-1 flex-col">{children}</div>
      <footer className="mx-auto w-full max-w-studio px-4 py-8 text-caption text-subtle-foreground sm:px-6">
        Clipah turns long videos into short clips you choose, edit, and publish.
      </footer>
    </div>
  )
}
