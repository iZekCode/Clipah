import { useId, type ReactElement, type ReactNode } from 'react'
import { cloneElement } from 'react'

/** The one look every text input shares. */
export const inputClassName =
  'h-9 w-full rounded-md border border-input bg-secondary px-3 text-small text-foreground placeholder:text-subtle-foreground disabled:opacity-60'

/**
 * A labelled form control with optional help text.
 *
 * The label is a real `<label>` bound to the control, and help text is announced with it,
 * so a screen reader hears the same guidance a sighted member reads.
 */
export function Field({
  label,
  help,
  children,
}: {
  label: ReactNode
  help?: ReactNode
  children: ReactElement<{ id?: string; 'aria-describedby'?: string }>
}) {
  const id = useId()
  const helpId = `${id}-help`
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-small font-medium">
        {label}
      </label>
      {cloneElement(children, {
        id,
        ...(help === undefined ? {} : { 'aria-describedby': helpId }),
      })}
      {help === undefined ? null : (
        <p id={helpId} className="text-caption text-muted-foreground">
          {help}
        </p>
      )}
    </div>
  )
}
