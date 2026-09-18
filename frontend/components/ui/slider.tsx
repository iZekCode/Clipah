import { forwardRef, type InputHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/** A native range input in Signal's skin; the keyboard and screen readers get it for free. */
export const Slider = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>>(
  function Slider({ className, ...props }, ref) {
    return <input ref={ref} type="range" className={cn('signal-range w-full', className)} {...props} />
  },
)
