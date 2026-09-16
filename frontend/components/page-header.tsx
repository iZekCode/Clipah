import Link from 'next/link'
import type { ReactNode } from 'react'

/** One step back up the hierarchy, shown above a page title. */
export interface Crumb {
  href: string
  label: string
}

/**
 * The top of every page: where the member is, what the page holds, and the one thing
 * they are most likely to do next.
 *
 * The primary action sits beside the title; everything else a page offers belongs in
 * context next to the thing it acts on, not up here competing with it.
 */
export function PageHeader({
  title,
  description,
  crumbs = [],
  actions,
  meta,
}: {
  title: ReactNode
  description?: ReactNode
  crumbs?: Crumb[]
  actions?: ReactNode
  meta?: ReactNode
}) {
  return (
    <header className="flex flex-col gap-4 pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 space-y-1.5">
        {crumbs.length === 0 ? null : (
          <nav aria-label="Breadcrumb">
            <ol className="flex flex-wrap items-center gap-1 text-sm text-muted-foreground">
              {crumbs.map((crumb) => (
                <li key={crumb.href} className="flex items-center gap-1">
                  <Link href={crumb.href} className="rounded hover:text-foreground">
                    {crumb.label}
                  </Link>
                  <span aria-hidden="true">/</span>
                </li>
              ))}
            </ol>
          </nav>
        )}
        <h1 className="truncate text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1>
        {description === undefined ? null : (
          <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>
        )}
        {meta === undefined ? null : (
          <div className="flex flex-wrap items-center gap-2 pt-1">{meta}</div>
        )}
      </div>
      {actions === undefined ? null : (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      )}
    </header>
  )
}

/** A titled group of content inside a page. */
export function Section({
  title,
  description,
  actions,
  children,
  labelledBy,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  labelledBy?: string
}) {
  return (
    <section aria-labelledby={labelledBy} className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="space-y-0.5">
          <h2 id={labelledBy} className="text-base font-semibold tracking-tight">
            {title}
          </h2>
          {description === undefined ? null : (
            <p className="text-sm text-muted-foreground">{description}</p>
          )}
        </div>
        {actions}
      </div>
      {children}
    </section>
  )
}
