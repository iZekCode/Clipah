import Link from 'next/link'

import { PublicFrame } from '@/components/public-frame'

/**
 * A read-only example of what Clipah produces.
 *
 * Everything on this page is bundled with the application and labelled as an example. It
 * names no Workspace, no User, and no Project that exists, so an anonymous visitor sees the
 * shape of the work without any private endpoint being called on their behalf.
 */

const DEMO_CANDIDATES = [
  {
    hook: 'The one question that changed the interview',
    reason: 'A complete answer with a clear setup and payoff, spoken by a single speaker.',
    length: '0:32',
    category: 'Insight',
    score: 91,
    gradient: 'from-violet-500 to-fuchsia-500',
  },
  {
    hook: 'Why the first attempt failed',
    reason: 'A self-contained story beat that does not depend on earlier context.',
    length: '0:41',
    category: 'Story',
    score: 86,
    gradient: 'from-amber-400 to-rose-500',
  },
  {
    hook: 'A number nobody expected',
    reason: 'A concrete claim followed immediately by the evidence behind it.',
    length: '0:27',
    category: 'Data',
    score: 82,
    gradient: 'from-sky-500 to-emerald-500',
  },
] as const

/** The public demo project, rendered entirely from bundled example data. */
export default function DemoPage() {
  return (
    <PublicFrame>
      <main className="mx-auto w-full max-w-6xl space-y-8 px-6 py-10">
        <div className="space-y-3">
          <p className="inline-flex rounded-full bg-warning-soft px-3 py-1 text-xs font-medium text-warning">
            Example content — not a real project
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">Demo project: a 58-minute interview</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            This is what you see after adding a video: suggested moments, ranked, each with the
            reason it was chosen. Sign in to run this on your own video.
          </p>
        </div>
        <ul aria-label="Example moments" className="grid gap-4 md:grid-cols-3">
          {DEMO_CANDIDATES.map((candidate, index) => (
            <li key={candidate.hook} className="surface overflow-hidden">
              <div className={`flex aspect-video items-end bg-gradient-to-br p-3 ${candidate.gradient}`}>
                <span className="rounded bg-black/40 px-2 py-0.5 text-xs font-medium text-white">
                  {candidate.length}
                </span>
              </div>
              <div className="space-y-2 p-5">
                <p className="text-xs text-muted-foreground">
                  #{index + 1} · {candidate.category} · Score {candidate.score}
                </p>
                <p className="text-base font-semibold">{candidate.hook}</p>
                <p className="text-sm text-muted-foreground">{candidate.reason}</p>
              </div>
            </li>
          ))}
        </ul>
        <div className="surface flex flex-col items-start justify-between gap-4 p-6 sm:flex-row sm:items-center">
          <div>
            <p className="font-semibold">Ready to try it with your own video?</p>
            <p className="text-sm text-muted-foreground">
              Your first project takes a few minutes to set up.
            </p>
          </div>
          <Link
            href="/signin"
            className="inline-flex h-10 items-center rounded-lg bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            Get started
          </Link>
        </div>
      </main>
    </PublicFrame>
  )
}
