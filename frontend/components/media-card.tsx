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
  corner,
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
  /** One control on the picture's top-right corner, above the card's link. */
  corner?: ReactNode
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
      // No overflow clipping here: the item menu opens below the card and must not be cut off.
      className="group relative flex flex-col rounded-lg border bg-card transition-colors duration-fast ease-signal focus-within:border-line-strong hover:border-line-strong"
    >
      <CardFocusContext.Provider value={focused}>
        <div
          className={cn(
            'relative overflow-hidden rounded-t-[inherit] bg-stage',
            aspect === 'video' ? 'aspect-video' : 'aspect-[9/16]',
          )}
        >
          {thumbnail}
          {status === undefined ? null : <div className="absolute bottom-2 left-2">{status}</div>}
          {corner === undefined ? null : (
            <div className="absolute right-2 top-2 z-10">{corner}</div>
          )}
        </div>
      </CardFocusContext.Provider>
      <div className="flex flex-1 items-start gap-2 p-3">
        <div className="min-w-0 flex-1 space-y-0.5">
          {/* A hidden title hides only its words: clipping the heading would clip the link's
              stretched hit area to one pixel, and the card would stop opening. */}
          <h3 className={cn('truncate text-small font-semibold', hideTitle && 'h-0')}>
            <Link href={href} className="after:absolute after:inset-0 after:content-['']">
              {hideTitle ? <span className="sr-only">{title}</span> : title}
            </Link>
          </h3>
          {subtitle === undefined ? null : (
            <div className="truncate text-caption text-muted-foreground">{subtitle}</div>
          )}
          {footer === undefined ? null : (
            // With nothing visible above it, the footer sits on the card's own padding.
            <div
              className={cn(
                'relative z-10',
                (!hideTitle || subtitle !== undefined) && 'pt-1.5',
              )}
            >
              {footer}
            </div>
          )}
        </div>
        {menu === undefined ? null : <div className="relative z-10 shrink-0">{menu}</div>}
      </div>
    </article>
  )
}
