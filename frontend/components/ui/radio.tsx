import { forwardRef, type InputHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/** A native radio button in Signal's skin. */
export const Radio = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>>(
  function Radio({ className, ...props }, ref) {
    return <input ref={ref} type="radio" className={cn('signal-radio', className)} {...props} />
  },
)
