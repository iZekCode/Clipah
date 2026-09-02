/**
 * A read-only example of what Clipah produces.
 *
 * Everything on this page is bundled with the application. It names no Workspace, no
 * User, and no Project that exists, so an anonymous visitor sees the shape of the work
 * without any private endpoint being called on their behalf.
 */

const DEMO_CANDIDATES = [
  {
    hook: 'The one question that changed the interview',
    reason: 'A complete answer with a clear setup and payoff, spoken by a single speaker.',
    length: '0:32',
  },
  {
    hook: 'Why the first attempt failed',
    reason: 'A self-contained story beat that does not depend on earlier context.',
    length: '0:41',
  },
  {
    hook: 'A number nobody expected',
    reason: 'A concrete claim followed immediately by the evidence behind it.',
    length: '0:27',
  },
] as const

/** The public demo project, rendered entirely from bundled example data. */
export default function DemoPage() {
  return (
    <main className="mx-auto max-w-3xl space-y-8 px-6 py-16">
      <div className="space-y-3">
        <h1 className="text-3xl font-semibold tracking-tight">Demo project</h1>
        <p className="text-sm text-muted-foreground">
          An example of ranked clip candidates, with the reason each one was chosen. Sign in
          to run this on your own video.
        </p>
      </div>
      <ul className="divide-y rounded-lg border">
        {DEMO_CANDIDATES.map((candidate, index) => (
          <li key={candidate.hook} className="space-y-1 px-4 py-4">
            <p className="text-sm font-medium">
              {index + 1}. {candidate.hook}
            </p>
            <p className="text-sm text-muted-foreground">{candidate.reason}</p>
            <p className="text-xs text-muted-foreground">{candidate.length}</p>
          </li>
        ))}
      </ul>
    </main>
  )
}
