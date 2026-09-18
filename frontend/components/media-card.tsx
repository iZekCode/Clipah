'use client'

import Link from 'next/link'
import { useState, type ReactNode } from 'react'

import { CardFocusContext } from '@/components/media/card-focus'
import { cn } from '@/lib/utils'

/**
 * One piece of media in a grid: a picture, a name that opens it, and its state.
 *
 * The whole card is the link through the title's stretched hit area; contextual actions sit
 * in `menu`, above the link, so opening a menu never navigates.
 */
export function MediaCard({
  href,
  title,
  thumbnail,
  subtitle,
  status,
  menu,
  footer,
  aspect = 'video',
  hideTitle = false,
}: {
  href: string
  title: string
  thumbnail: ReactNode
  subtitle?: ReactNode
  status?: ReactNode
  menu?: ReactNode
  footer?: ReactNode
  aspect?: 'video' | 'portrait'
  hideTitle?: boolean
}) {
  const [focused, setFocused] = useState(false)
  return (
    <article
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false)
      }}
      className="group relative flex flex-col overflow-hidden rounded-lg border bg-card transition-colors duration-fast ease-signal focus-within:border-line-strong hover:border-line-strong"
    >
      <CardFocusContext.Provider value={focused}>
        <div
          className={cn(
            'relative overflow-hidden bg-stage',
            aspect === 'video' ? 'aspect-video' : 'aspect-[9/16]',
          )}
        >
          {thumbnail}
          {status === undefined ? null : <div className="absolute bottom-2 left-2">{status}</div>}
        </div>
      </CardFocusContext.Provider>
      <div className="flex flex-1 items-start gap-2 p-3">
        <div className="min-w-0 flex-1 space-y-0.5">
          <h3 className={cn('truncate text-small font-semibold', hideTitle && 'sr-only')}>
            <Link href={href} className="after:absolute after:inset-0 after:content-['']">
              {title}
            </Link>
          </h3>
          {subtitle === undefined ? null : (
            <div className="truncate text-caption text-muted-foreground">{subtitle}</div>
          )}
          {footer === undefined ? null : <div className="relative z-10 pt-1.5">{footer}</div>}
        </div>
        {menu === undefined ? null : <div className="relative z-10 shrink-0">{menu}</div>}
      </div>
    </article>
  )
}
