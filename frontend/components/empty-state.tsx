import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

/**
 * Say what belongs here, why it is empty, and how to fill it.
 *
 * An empty page that only says "nothing here" leaves a creator guessing whether they did
 * something wrong; this always names the next step when there is one.
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
      className={`flex flex-col items-center justify-center rounded-xl border border-dashed bg-card/60 text-center ${
        compact ? 'gap-2 px-4 py-8' : 'gap-3 px-6 py-14'
      }`}
    >
      {Icon === undefined ? null : (
        <span className="flex size-11 items-center justify-center rounded-full bg-accent text-accent-foreground">
          <Icon aria-hidden="true" className="size-5" />
        </span>
      )}
      <p className="text-base font-medium">{title}</p>
      {description === undefined ? null : (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      )}
      {action === undefined ? null : <div className="pt-1">{action}</div>}
    </div>
  )
}
