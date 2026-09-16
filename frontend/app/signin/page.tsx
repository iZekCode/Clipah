import Link from 'next/link'

import { PublicFrame } from '@/components/public-frame'

/** The one way in: the backend starts the Google flow and sets the Session cookie. */
export default function SignInPage() {
  return (
    <PublicFrame signInLink={false}>
      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="surface w-full max-w-md space-y-6 p-8 shadow-md">
          <div className="space-y-2 text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Sign in to Clipah</h1>
            <p className="text-sm text-muted-foreground">
              New here? Signing in creates your own workspace, ready for your first video.
            </p>
          </div>
          <a
            href="/api/v1/auth/google/start"
            className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-primary text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            Continue with Google
          </a>
          <p className="text-center text-sm text-muted-foreground">
            Not ready yet?{' '}
            <Link href="/demo" className="font-medium text-primary hover:underline">
              See a demo first
            </Link>
          </p>
        </div>
      </main>
    </PublicFrame>
  )
}
