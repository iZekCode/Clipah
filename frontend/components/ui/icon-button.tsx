'use client'

import { cva } from 'class-variance-authority'
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

const iconButtonVariants = cva(
  'inline-flex shrink-0 items-center justify-center rounded-md transition-colors duration-fast ease-signal disabled:pointer-events-none disabled:opacity-40 [&_svg]:size-4',
  {
    variants: {
      variant: {
        ghost: 'text-muted-foreground hover:bg-secondary hover:text-foreground aria-pressed:text-primary',
        secondary: 'border border-line-strong bg-secondary text-foreground hover:border-input aria-pressed:text-primary',
        primary: 'bg-primary text-primary-foreground hover:bg-primary-hover',
      },
      size: { sm: 'size-8', md: 'size-9' },
    },
    defaultVariants: { variant: 'ghost', size: 'md' },
  },
)

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  /** The accessible name, also shown in the tooltip. */
  label: string
  icon: ReactNode
  /** The keyboard shortcut as a member reads it, for example `⌘Z`. */
  shortcut?: string
  variant?: 'ghost' | 'secondary' | 'primary'
  size?: 'sm' | 'md'
  tooltipSide?: 'top' | 'right' | 'bottom' | 'left'
}

/**
 * A square button that is only an icon, and therefore always carries a name.
 *
 * The tooltip repeats the name and adds the shortcut, so a member learns the keyboard from
 * the pointer. It brings its own provider, so it works outside the application shell too.
 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, shortcut, variant, size, tooltipSide = 'bottom', className, type = 'button', ...props },
  ref,
) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            ref={ref}
            type={type}
            aria-label={label}
            className={cn(iconButtonVariants({ variant, size }), className)}
            {...props}
          >
            {icon}
          </button>
        </TooltipTrigger>
        <TooltipContent side={tooltipSide}>
          <span>{label}</span>
          {shortcut === undefined ? null : (
            <kbd className="ml-2 font-mono text-caption text-subtle-foreground">{shortcut}</kbd>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
})
