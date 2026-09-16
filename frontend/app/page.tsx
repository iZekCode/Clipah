import { Clapperboard, Scissors, Send, Upload } from 'lucide-react'
import Link from 'next/link'

import { PublicFrame } from '@/components/public-frame'

const STEPS = [
  {
    icon: Upload,
    title: 'Add a video',
    description: 'Upload a podcast, interview, or livestream recording — or paste a public YouTube link.',
  },
  {
    icon: Clapperboard,
    title: 'Choose your moments',
    description: 'Clipah transcribes it and suggests the strongest moments, with the reason each one works.',
  },
  {
    icon: Scissors,
    title: 'Edit and export',
    description: 'Style captions, reframe for vertical video, add B-roll, then export in the shape you need.',
  },
  {
    icon: Send,
    title: 'Publish',
    description: 'Download the file, or send it to YouTube Shorts, Instagram Reels, or TikTok when connected.',
  },
] as const

/** The public landing page: what a creator gets, how it works, and the one way in. */
export default function LandingPage() {
  return (
    <PublicFrame>
      <main className="mx-auto w-full max-w-6xl px-6">
        <section className="grid items-center gap-10 py-12 lg:grid-cols-2 lg:py-20">
          <div className="space-y-6">
            <p className="inline-flex rounded-full bg-accent px-3 py-1 text-xs font-medium text-accent-foreground">
              For solo creators
            </p>
            <h1 className="text-balance text-4xl font-semibold tracking-tight sm:text-5xl">
              Clipah turns one long video into short clips worth sharing
            </h1>
            <p className="max-w-xl text-lg text-muted-foreground">
              Find the best moments without scrubbing through hours of footage. Review them,
              polish them in a focused editor, and publish — all in one place.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link
                href="/signin"
                className="inline-flex h-11 items-center rounded-lg bg-primary px-6 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
              >
                Get started free
              </Link>
              <Link
                href="/demo"
                className="inline-flex h-11 items-center rounded-lg border bg-card px-6 text-sm font-medium hover:bg-secondary"
              >
                See an example
              </Link>
            </div>
          </div>
          <div aria-hidden="true" className="surface overflow-hidden p-4 shadow-lg">
            <div className="grid grid-cols-3 gap-3">
              {['0:32', '0:41', '0:27'].map((length, index) => (
                <div key={length} className="space-y-2">
                  <div
                    className={`aspect-[9/16] rounded-lg bg-gradient-to-br ${
                      index === 0
                        ? 'from-violet-500 to-fuchsia-500'
                        : index === 1
                          ? 'from-amber-400 to-rose-500'
                          : 'from-sky-500 to-emerald-500'
                    } flex items-end p-2`}
                  >
                    <span className="rounded bg-black/40 px-1.5 py-0.5 text-[10px] font-medium text-white">
                      {length}
                    </span>
                  </div>
                  <div className="h-2 w-4/5 rounded bg-muted" />
                  <div className="h-2 w-1/2 rounded bg-muted" />
                </div>
              ))}
            </div>
          </div>
        </section>

        <section aria-labelledby="how-it-works" className="space-y-6 py-12">
          <h2 id="how-it-works" className="text-2xl font-semibold tracking-tight">
            How it works
          </h2>
          <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, index) => {
              const Icon = step.icon
              return (
                <li key={step.title} className="surface space-y-3 p-5">
                  <span className="flex size-10 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                    <Icon aria-hidden="true" className="size-5" />
                  </span>
                  <h3 className="text-sm font-semibold">
                    {index + 1}. {step.title}
                  </h3>
                  <p className="text-sm text-muted-foreground">{step.description}</p>
                </li>
              )
            })}
          </ol>
        </section>
      </main>
    </PublicFrame>
  )
}
