'use client'

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
 * The choice is deliberately inert until a member has asked for suggestions at least
 * once: a new clip has no plan, so offering to change the coverage of nothing would be
 * a control that does not control anything.
 */
export function CoverageControl({
  coverage,
  enabled,
  busy,
  onCoverage,
  onSuggest,
}: {
  coverage: BrollCoverage
  enabled: boolean
  busy: boolean
  onCoverage: (coverage: BrollCoverage) => void
  onSuggest: () => void
}) {
  return (
    <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-1 text-xs">
        <span className="font-medium">Coverage</span>
        <select
          value={coverage}
          disabled={!enabled || busy}
          onChange={(event) => onCoverage(event.target.value as BrollCoverage)}
          className="rounded border px-2 py-1 text-xs"
        >
          {(Object.keys(COVERAGE_LABELS) as BrollCoverage[]).map((option) => (
            <option key={option} value={option}>
              {COVERAGE_LABELS[option]}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        disabled={busy}
        onClick={onSuggest}
        className="rounded border px-3 py-1 text-xs font-medium"
      >
        {busy ? 'Looking for B-roll…' : 'Suggest B-roll'}
      </button>
    </div>
  )
}
