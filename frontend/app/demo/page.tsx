import Link from 'next/link'

import { DesignedFrame } from '@/components/media/poster'
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
    startMs: 754_000,
    endMs: 786_000,
    category: 'Insight',
    score: 91,
    scores: { Hook: 94, Payoff: 90, 'Narrative completeness': 88 },
  },
  {
    hook: 'Why the first attempt failed',
    reason: 'A self-contained story beat that does not depend on earlier context.',
    startMs: 1_512_000,
    endMs: 1_553_000,
    category: 'Story',
    score: 86,
    scores: { Hook: 84, Payoff: 88, 'Narrative completeness': 86 },
  },
  {
    hook: 'A number nobody expected',
    reason: 'A concrete claim followed immediately by the evidence behind it.',
    startMs: 2_890_000,
    endMs: 2_917_000,
    category: 'Data',
    score: 82,
    scores: { Hook: 85, Payoff: 80, 'Narrative completeness': 81 },
  },
] as const

/** The public demo project, rendered entirely from bundled example data in review layout. */
export default function DemoPage() {
  const chosen = DEMO_CANDIDATES[0]
  return (
    <PublicFrame>
      <main className="mx-auto w-full max-w-studio space-y-8 px-4 py-10 sm:px-6">
        <div className="space-y-3">
          <p className="text-caption font-medium uppercase tracking-wide text-warning">
            Example content — not a real project
          </p>
          <h1 className="font-display text-h1">Demo project: a 58-minute interview</h1>
          <p className="max-w-2xl text-small text-muted-foreground">
            This is what you see after adding a video: suggested moments, ranked, each with the
            reason it was chosen. Sign in to run this on your own video.
          </p>
        </div>
        <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)_minmax(0,1.2fr)]">
          <ul aria-label="Example moments" className="space-y-2">
            {DEMO_CANDIDATES.map((candidate, index) => (
              <li
                key={candidate.hook}
                className={
                  index === 0
                    ? 'rounded-md border border-primary p-3'
                    : 'rounded-md border border-border p-3'
                }
              >
                <p className="font-mono text-caption text-primary">
                  #{index + 1} · {candidate.category} · {candidate.score}
                </p>
                <p className="text-small font-medium">{candidate.hook}</p>
              </li>
            ))}
          </ul>
          <div className="relative mx-auto aspect-[9/16] w-full max-w-[320px] overflow-hidden rounded-lg">
            <DesignedFrame startMs={chosen.startMs} endMs={chosen.endMs} />
            <p className="font-display absolute inset-x-4 bottom-10 text-center text-h2 uppercase leading-tight text-foreground">
              {chosen.hook}
            </p>
          </div>
          <section aria-label="Why this moment" className="space-y-4">
            <p className="font-display text-h2 uppercase">{chosen.hook}</p>
            <p className="text-small text-muted-foreground">{chosen.reason}</p>
            <dl className="space-y-3">
              {Object.entries(chosen.scores).map(([label, value]) => (
                <div key={label} className="grid grid-cols-[minmax(0,1fr)_auto] gap-y-1 text-caption">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="font-mono">{value}</dd>
                  <dd aria-hidden="true" className="col-span-2 h-1 rounded-full bg-secondary">
                    <div className="h-full rounded-full bg-foreground" style={{ width: `${value}%` }} />
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        </div>
        <div className="flex flex-col items-start justify-between gap-4 rounded-lg border p-6 sm:flex-row sm:items-center">
          <div>
            <p className="text-title">Ready to try it with your own video?</p>
            <p className="text-small text-muted-foreground">
              Your first project takes a few minutes to set up.
            </p>
          </div>
          <Link
            href="/signin"
            className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-small font-semibold text-primary-foreground transition-colors duration-fast ease-signal hover:bg-primary-hover"
          >
            Get started
          </Link>
        </div>
      </main>
    </PublicFrame>
  )
}
