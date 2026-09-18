import type { CandidateResponse } from '@/lib/api/generated/model'

/** The seven dimensions behind a score, in the order the analysis reasons about them. */
const DIMENSIONS = [
  ['Hook', 'hook'],
  ['Payoff', 'payoff'],
  ['Narrative completeness', 'narrativeCompleteness'],
  ['Context safety', 'contextSafety'],
  ['Platform fit', 'platformFit'],
  ['Transcript confidence', 'transcriptConfidence'],
  ['Visual opportunity', 'visualOpportunity'],
] as const

/** The seven dimensions behind a score, as labelled bars with their numbers. */
export function ScoreBars({ breakdown }: { breakdown: CandidateResponse['scoreBreakdown'] }) {
  return (
    <dl aria-label="Score breakdown" className="space-y-2">
      {DIMENSIONS.map(([label, key]) => {
        const value = Math.round(breakdown[key] * 100)
        return (
          <div
            key={key}
            className="grid grid-cols-[minmax(0,1fr)_2.5rem] items-center gap-x-3 gap-y-1"
          >
            <dt className="text-caption text-muted-foreground">{label}</dt>
            <dd className="tabular text-right font-mono text-caption">{value}</dd>
            <span
              aria-hidden="true"
              className="col-span-2 block h-1 overflow-hidden rounded-full bg-secondary"
            >
              <span className="block h-full bg-foreground" style={{ width: `${value}%` }} />
            </span>
          </div>
        )
      })}
    </dl>
  )
}
