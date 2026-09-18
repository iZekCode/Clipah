'use client'

import { Toaster as Sonner } from 'sonner'

type ToasterProps = React.ComponentProps<typeof Sonner>

/** Signal's toasts: dark, bottom centre, on the overlay surface. */
export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="dark"
      position="bottom-center"
      toastOptions={{
        classNames: {
          toast:
            'rounded-lg border border-line-strong bg-popover text-popover-foreground text-small shadow-xl',
          description: 'text-muted-foreground',
          actionButton: 'rounded-md bg-primary text-primary-foreground font-medium',
          cancelButton: 'rounded-md bg-secondary text-foreground',
        },
      }}
      {...props}
    />
  )
}
