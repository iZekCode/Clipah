'use client'

import type { AccessibilityWarningResponse } from '@/lib/api/generated/model'

export interface AccessibilitySelection {
  itemId: string | null
  timeMs: number | null
}

/** Actionable, keyboard-reachable warnings for the immutable Revision on screen. */
export function AccessibilityPanel({
  warnings,
  onSelect,
}: {
  warnings: AccessibilityWarningResponse[]
  onSelect: (selection: AccessibilitySelection) => void
}) {
  return (
    <section aria-labelledby="accessibility-quality-heading" className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h2 id="accessibility-quality-heading" className="text-sm font-semibold">
          Accessibility quality
        </h2>
        <span className="text-caption text-muted-foreground">{warnings.length} warnings</span>
      </div>
      {warnings.length === 0 ? (
        <p role="status" className="text-small text-muted-foreground">
          No accessibility warnings for this revision.
        </p>
      ) : (
        <ul className="space-y-2">
          {warnings.map((warning, index) => (
            <li key={`${warning.code}-${warning.itemId ?? ''}-${warning.startMs ?? index}`}>
              <button
                type="button"
                aria-label={warning.action}
                onClick={() =>
                  onSelect({ itemId: warning.itemId ?? null, timeMs: warning.startMs ?? null })
                }
                className="w-full rounded-md bg-warning-soft p-2 text-left text-small text-warning focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
              >
                <span className="block font-medium">{warning.action}</span>
                {warning.measured == null || warning.threshold == null ? null : (
                  <span className="text-caption text-muted-foreground">
                    Measured {warning.measured}; threshold {warning.threshold}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
