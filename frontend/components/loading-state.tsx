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
      <p role="status" className="text-sm text-muted-foreground">
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
            <div key={index} className="surface overflow-hidden">
              <div className="aspect-video animate-pulse bg-muted" />
              <div className="space-y-2 p-4">
                <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
                <div className="h-3 w-1/3 animate-pulse rounded bg-muted" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div aria-hidden="true" className="surface divide-y">
          {Array.from({ length: count }, (_, index) => (
            <div key={index} className="flex items-center gap-3 p-4">
              <div className="size-10 animate-pulse rounded-lg bg-muted" />
              <div className="flex-1 space-y-2">
                <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
                <div className="h-3 w-1/4 animate-pulse rounded bg-muted" />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
