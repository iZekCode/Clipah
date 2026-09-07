'use client'

import type { ContextWarningResponse } from '@/lib/api/generated/model'

/** What each warning type means, in the words a member is owed rather than an enum value. */
const EXPLANATIONS: Record<string, string> = {
  cut_off_question: 'Cut off question — the clip asks something it never answers.',
  cut_off_payoff: 'Cut off payoff — the clip stops before the sentence finishes.',
  missing_negation: 'Missing negation — the words that reverse this claim fall outside the cut.',
  missing_attribution: 'Missing attribution — the clip drops who was being quoted.',
  unsupported_reference: 'Unsupported reference — the clip opens on a word whose subject is cut.',
  omitted_caveat: 'Omitted caveat — the qualifier attached to this claim falls outside the cut.',
  incomplete_list: 'Incomplete list — the clip shows part of an enumeration as if it were all.',
  claim_needs_source: 'Claim needs a source — this statement would be stronger with a citation.',
}

/**
 * Every reason this boundary might misrepresent the speaker, shown before anyone edits.
 *
 * A warning is only useful if it can be checked, so each one names the words that prove it
 * and the boundary that would resolve it. Nothing here moves a boundary: the member decides.
 */
export function ContextWarnings({ warnings }: { warnings: ContextWarningResponse[] }) {
  if (warnings.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No context warnings. This cut reads as a complete thought.
      </p>
    )
  }

  return (
    <ul aria-label="Context warnings" className="flex flex-col gap-2 text-xs">
      {warnings.map((warning) => (
        <li
          key={`${warning.type}:${warning.evidenceWordIds.join(',')}`}
          className="rounded border p-2"
        >
          <p className="font-medium">{EXPLANATIONS[warning.type] ?? warning.type}</p>
          <p className="text-muted-foreground">
            Severity: {warning.severity} · Evidence: {warning.evidenceWordIds.join(', ')}
          </p>
          {warning.suggestedStartWordId === null && warning.suggestedEndWordId === null ? null : (
            <p className="text-muted-foreground">
              A safer boundary would run{' '}
              {warning.suggestedStartWordId ?? 'the current start'} to{' '}
              {warning.suggestedEndWordId ?? 'the current end'}.
            </p>
          )}
        </li>
      ))}
    </ul>
  )
}
