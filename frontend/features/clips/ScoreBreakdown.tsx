'use client'

/**
 * Why one candidate ranked where it did, as figures rather than prose.
 *
 * The analyzer records a breakdown per candidate, and a member deciding whether to edit a
 * clip is entitled to see it. What is absent is shown as absent: an empty breakdown says
 * so instead of drawing zeros, because a zero is a claim and a missing value is not.
 */
export function ScoreBreakdown({
  score,
  breakdown,
}: {
  score: number
  breakdown: Record<string, number>
}) {
  const components = Object.entries(breakdown)

  return (
    <section aria-label="Score breakdown" className="flex flex-col gap-2 text-xs">
      <p className="font-medium">Overall {formatPercent(score)}</p>
      {components.length === 0 ? (
        <p className="text-muted-foreground">
          No score breakdown was recorded for this clip.
        </p>
      ) : (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
          {components.map(([name, value]) => (
            <div key={name} className="contents">
              <dt className="text-muted-foreground">{humanize(name)}</dt>
              <dd>{formatPercent(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  )
}

/** Read a fraction as the percentage a member compares clips on. */
function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`
}

/** Render a stored component name as the words it stands for. */
function humanize(name: string): string {
  return name.replaceAll('_', ' ')
}
