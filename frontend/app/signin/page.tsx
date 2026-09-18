import Link from 'next/link'

import { GoogleSignIn } from '@/components/google-sign-in'
import { MarketingFrame } from '@/components/marketing-frame'
import { PublicFrame } from '@/components/public-frame'

/** The one way in: the product on the left, Google sign-in on the right. */
export default function SignInPage() {
  return (
    <PublicFrame signInLink={false}>
      <main className="mx-auto grid w-full max-w-studio flex-1 items-center gap-12 px-4 py-12 sm:px-6 lg:min-h-[calc(100vh-8rem)] lg:grid-cols-[3fr_2fr]">
        <MarketingFrame
          still="/marketing/edit.png"
          alt="The Clipah editor"
          className="hidden lg:block"
        />
        <div className="mx-auto w-full max-w-sm space-y-6">
          <div className="space-y-2">
            <h1 className="font-display text-h1">Sign in to Clipah</h1>
            <p className="text-small text-muted-foreground">
              New here? Signing in creates your studio, ready for your first video.
            </p>
          </div>
          <GoogleSignIn />
          <p className="text-small text-muted-foreground">
            Not ready yet?{' '}
            <Link href="/demo" className="font-semibold text-foreground hover:text-primary">
              See an example
            </Link>
          </p>
        </div>
      </main>
    </PublicFrame>
  )
}
