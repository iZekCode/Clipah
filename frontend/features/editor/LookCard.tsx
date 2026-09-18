import { cn } from '@/lib/utils'

import { LookSample } from './LookSample'
import type { TemplateDefinition } from './templates'

/** One template drawn as the caption it produces, on a small 9:16 frame. */
export function LookCard({
  template,
  applied,
  onApply,
}: {
  template: TemplateDefinition
  applied: boolean
  onApply: () => void
}) {
  return (
    <button
      type="button"
      aria-pressed={applied}
      onClick={onApply}
      className={cn(
        'group flex min-w-0 flex-col gap-1.5 rounded-md border p-1.5 text-left transition-colors duration-fast ease-signal',
        applied ? 'border-primary' : 'border-border hover:border-line-strong',
      )}
    >
      <LookSample captionStyle={template.captionStyle} captionMode={template.captionMode} />
      <span className="truncate text-caption font-semibold text-foreground">{template.name}</span>
      <span className="line-clamp-2 text-[11px] text-muted-foreground">
        {template.description}
      </span>
    </button>
  )
}
