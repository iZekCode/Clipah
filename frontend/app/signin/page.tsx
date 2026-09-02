import Link from 'next/link'

/** The one way in: the backend starts the Google flow and sets the Session cookie. */
export default function SignInPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-6">
      <h1 className="text-2xl font-semibold tracking-tight">Sign in to Clipah</h1>
      <p className="text-sm text-muted-foreground">
        Signing in creates your personal workspace if you do not have one yet.
      </p>
      <a
        href="/api/v1/auth/google/start"
        className="inline-flex w-fit rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
      >
        Continue with Google
      </a>
      <Link href="/demo" className="text-sm underline">
        See a demo first
      </Link>
    </main>
  )
}
