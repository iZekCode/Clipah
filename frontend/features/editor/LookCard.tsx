import { cn } from '@/lib/utils'

import { captionFontStack } from './caption-fonts'
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
  const style = template.captionStyle
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
      <span
        aria-hidden="true"
        className="relative flex aspect-[9/16] items-end justify-center overflow-hidden rounded-sm bg-stage p-2"
      >
        <span
          data-testid="look-sample"
          style={{
            fontFamily: captionFontStack(style.fontFamily),
            fontWeight: style.weight,
            fontStyle: style.italic ? 'italic' : 'normal',
            color: style.color,
            textAlign: style.align,
            letterSpacing: `${style.letterSpacing / 4}px`,
            backgroundColor: style.backgroundEnabled ? style.backgroundColor : undefined,
          }}
          className="px-1 text-[15px] leading-tight"
        >
          Say it <span style={{ color: style.highlightColor }}>loud</span>
        </span>
      </span>
      <span className="truncate text-caption font-semibold text-foreground">{template.name}</span>
      <span className="line-clamp-2 text-[11px] text-muted-foreground">
        {template.description}
      </span>
    </button>
  )
}
