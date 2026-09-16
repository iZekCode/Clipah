import { useId, type ReactElement, type ReactNode } from 'react'
import { cloneElement } from 'react'

/** The one look every text input shares. */
export const inputClassName =
  'h-10 w-full rounded-lg border border-input bg-card px-3 text-sm shadow-sm placeholder:text-muted-foreground disabled:opacity-60'

/** The one look every select shares. */
export const selectClassName =
  'h-9 rounded-lg border border-input bg-card px-2.5 text-sm shadow-sm disabled:opacity-60'

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
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      {cloneElement(children, {
        id,
        ...(help === undefined ? {} : { 'aria-describedby': helpId }),
      })}
      {help === undefined ? null : (
        <p id={helpId} className="text-xs text-muted-foreground">
          {help}
        </p>
      )}
    </div>
  )
}
