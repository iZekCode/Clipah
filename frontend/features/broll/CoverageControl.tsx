'use client'

import { Select } from '@/components/ui/select'
import type { BrollCoverage } from '@/lib/api/generated/model'

/** How busy each coverage makes the finished cut, in the words a member reads. */
const COVERAGE_LABELS: Record<BrollCoverage, string> = {
  minimal: 'Minimal — a cutaway only where the words need one',
  balanced: 'Balanced — a cutaway every few sentences',
  dynamic: 'Dynamic — as many cutaways as the clip will carry',
}

/**
 * The one choice a member makes about B-roll, and the action that asks for it.
 *
 * The coverage is sent with every request, the first included, so it can be chosen before
 * anything has been asked for; it is only held still while a request is in flight.
 */
export function CoverageControl({
  coverage,
  busy,
  onCoverage,
  onSuggest,
}: {
  coverage: BrollCoverage
  busy: boolean
  onCoverage: (coverage: BrollCoverage) => void
  onSuggest: () => void
}) {
  return (
    <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-1 text-xs">
        <span className="font-medium">Coverage</span>
        <Select
          value={coverage}
          disabled={busy}
          onChange={(event) => onCoverage(event.target.value as BrollCoverage)}
          controlSize="sm"
        >
          {(Object.keys(COVERAGE_LABELS) as BrollCoverage[]).map((option) => (
            <option key={option} value={option}>
              {COVERAGE_LABELS[option]}
            </option>
          ))}
        </Select>
      </label>
      <button
        type="button"
        disabled={busy}
        onClick={onSuggest}
        className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40"
      >
        {busy ? 'Looking for B-roll…' : 'Suggest B-roll'}
      </button>
    </div>
  )
}
