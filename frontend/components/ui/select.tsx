import { ChevronDown } from 'lucide-react'
import { forwardRef, type SelectHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  controlSize?: 'sm' | 'md'
  wrapperClassName?: string
}

/**
 * A native select in Signal's skin.
 *
 * Native on purpose: phones open their own picker, forms and labels work unaided, and the
 * operating system draws the option list in the page's dark colour scheme.
 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, wrapperClassName, controlSize = 'md', children, ...props },
  ref,
) {
  return (
    <span className={cn('relative inline-flex min-w-0', wrapperClassName)}>
      <select
        ref={ref}
        className={cn(
          'w-full min-w-0 appearance-none truncate rounded-md border border-input bg-secondary pl-3 pr-8 text-small text-foreground transition-colors duration-fast ease-signal hover:border-foreground/60 disabled:cursor-not-allowed disabled:opacity-50',
          controlSize === 'sm' ? 'h-8' : 'h-9',
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        strokeWidth={1.75}
        className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      />
    </span>
  )
})
