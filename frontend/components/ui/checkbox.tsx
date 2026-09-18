import { forwardRef, type InputHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/** A native checkbox in Signal's skin; checked is a lime square with a graphite tick. */
export const Checkbox = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>>(
  function Checkbox({ className, ...props }, ref) {
    return <input ref={ref} type="checkbox" className={cn('signal-checkbox', className)} {...props} />
  },
)
