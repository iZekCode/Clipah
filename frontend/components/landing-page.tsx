import Link from 'next/link'

import { MarketingFrame } from '@/components/marketing-frame'
import { PublicFrame } from '@/components/public-frame'

const FRAMES = [
  {
    still: '/marketing/review.png',
    title: 'Review',
    text: 'Every suggested moment, ranked, with the reason it works and the words around it.',
  },
  {
    still: '/marketing/edit.png',
    title: 'Edit',
    text: 'Captions you edit as text, a real timeline, and the crop on the picture itself.',
  },
  {
    still: '/marketing/publish.png',
    title: 'Publish',
    text: 'Download the file, or schedule it to YouTube Shorts, Instagram Reels, or TikTok.',
  },
] as const

/** The public landing page: what a creator gets, shown with the product itself. */
export function LandingPage() {
  return (
    <PublicFrame>
      <main className="mx-auto w-full max-w-studio px-4 sm:px-6">
        <section className="grid items-center gap-10 py-12 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:py-20">
          <div className="space-y-6">
            <h1 className="font-display text-balance text-display lg:text-hero">
              Long video in. Clips worth posting out.
            </h1>
            <p className="max-w-xl text-body text-muted-foreground">
              Drop in a podcast, interview, or stream. Clipah transcribes it, finds the moments
              worth sharing, and gives you a studio to finish and publish them.
            </p>
            <div className="flex flex-wrap items-center gap-4">
              <Link
                href="/signin"
                className="inline-flex h-11 items-center rounded-md bg-primary px-5 text-body font-semibold text-primary-foreground transition-colors duration-fast ease-signal hover:bg-primary-hover"
              >
                Get started
              </Link>
              <Link
                href="/demo"
                className="text-small font-semibold text-muted-foreground transition-colors duration-fast ease-signal hover:text-foreground"
              >
                See an example
              </Link>
            </div>
          </div>
          <MarketingFrame still="/marketing/review.png" alt="Clipah's review mode in use" />
        </section>
        <section aria-label="What you do in Clipah" className="space-y-16 py-12">
          {FRAMES.map((frame, index) => (
            <div key={frame.title} className="grid items-center gap-8 lg:grid-cols-2">
              <div className={index % 2 === 1 ? 'lg:order-2' : undefined}>
                <h2 className="font-display text-h1">{frame.title}</h2>
                <p className="mt-3 max-w-md text-body text-muted-foreground">{frame.text}</p>
              </div>
              <MarketingFrame still={frame.still} alt={`Clipah ${frame.title.toLowerCase()} screen`} />
            </div>
          ))}
        </section>
      </main>
    </PublicFrame>
  )
}
