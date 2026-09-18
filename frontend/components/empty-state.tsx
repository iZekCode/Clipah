import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

/**
 * Say what belongs here and how to fill it, as an invitation rather than an apology.
 *
 * Left-aligned and as wide as its region, so an empty grid still reads as the grid it will
 * become. An icon, when given, is drawn bare beside the title.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  compact = false,
}: {
  icon?: LucideIcon
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  compact?: boolean
}) {
  return (
    <div
      className={`flex flex-col items-start rounded-lg border border-dashed border-line-strong bg-card/40 ${
        compact ? 'gap-2 px-4 py-5' : 'gap-3 px-6 py-8'
      }`}
    >
      <p className="flex items-center gap-2 text-title">
        {Icon === undefined ? null : (
          <Icon aria-hidden="true" strokeWidth={1.75} className="size-5 text-muted-foreground" />
        )}
        {title}
      </p>
      {description === undefined ? null : (
        <p className="max-w-xl text-small text-muted-foreground">{description}</p>
      )}
      {action === undefined ? null : <div className="pt-1">{action}</div>}
    </div>
  )
}
