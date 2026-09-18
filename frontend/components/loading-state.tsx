/**
 * Say that something is loading, in words a screen reader announces, while the shape of
 * what is coming holds its place on screen.
 */
export function LoadingState({
  label,
  variant = 'inline',
  count = 3,
}: {
  label: string
  variant?: 'inline' | 'cards' | 'rows'
  count?: number
}) {
  if (variant === 'inline') {
    return (
      <p role="status" className="text-small text-muted-foreground">
        {label}
      </p>
    )
  }
  return (
    <div>
      <p role="status" className="sr-only">
        {label}
      </p>
      {variant === 'cards' ? (
        <div aria-hidden="true" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: count }, (_, index) => (
            <div key={index} className="overflow-hidden rounded-lg border bg-card">
              <div className="aspect-video bg-secondary motion-safe:animate-pulse" />
              <div className="space-y-2 p-4">
                <div className="h-4 w-2/3 rounded bg-secondary motion-safe:animate-pulse" />
                <div className="h-3 w-1/3 rounded bg-secondary motion-safe:animate-pulse" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div aria-hidden="true" className="divide-y rounded-lg border bg-card">
          {Array.from({ length: count }, (_, index) => (
            <div key={index} className="flex items-center gap-3 p-4">
              <div className="size-10 rounded-lg bg-secondary motion-safe:animate-pulse" />
              <div className="flex-1 space-y-2">
                <div className="h-4 w-1/2 rounded bg-secondary motion-safe:animate-pulse" />
                <div className="h-3 w-1/4 rounded bg-secondary motion-safe:animate-pulse" />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
