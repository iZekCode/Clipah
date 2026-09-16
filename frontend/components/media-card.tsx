'use client'

import { useQuery } from '@tanstack/react-query'
import { Film } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useRef, useState, type ReactNode } from 'react'

import type { ApiError } from '@/lib/api/client'
import type { MediaPreviewResponse } from '@/lib/api/generated/model'
import { thumbnailApiV1ProjectsProjectIdThumbnailGet } from '@/lib/api/generated/studio/studio'
import { cn } from '@/lib/utils'

/**
 * One piece of media in a grid: a picture, a name that opens it, and its state.
 *
 * The whole title is the link, so the card has one obvious destination; contextual
 * actions sit in `menu`, outside the link, so opening a menu never navigates.
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
}: {
  href: string
  title: string
  thumbnail: ReactNode
  subtitle?: ReactNode
  status?: ReactNode
  menu?: ReactNode
  footer?: ReactNode
  aspect?: 'video' | 'portrait'
}) {
  return (
    <article className="surface group relative flex flex-col overflow-hidden transition-shadow hover:shadow-md">
      <div
        className={cn(
          'relative overflow-hidden bg-muted',
          aspect === 'video' ? 'aspect-video' : 'aspect-[4/5]',
        )}
      >
        {thumbnail}
        {status === undefined ? null : <div className="absolute left-3 top-3">{status}</div>}
      </div>
      <div className="flex flex-1 items-start gap-2 p-4">
        <div className="min-w-0 flex-1 space-y-1">
          <h3 className="truncate text-sm font-semibold">
            <Link
              href={href}
              className="rounded after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
            >
              {title}
            </Link>
          </h3>
          {subtitle === undefined ? null : (
            <div className="text-xs text-muted-foreground">{subtitle}</div>
          )}
          {footer === undefined ? null : <div className="relative pt-2">{footer}</div>}
        </div>
        {menu === undefined ? null : <div className="relative z-10 shrink-0">{menu}</div>}
      </div>
    </article>
  )
}

/** The intentional stand-in for media that has not been produced or cannot be shown. */
export function MediaPlaceholder({ label }: { label: string }) {
  return (
    <div className="flex size-full flex-col items-center justify-center gap-2 bg-gradient-to-br from-accent to-secondary text-accent-foreground">
      <Film aria-hidden="true" className="size-7 opacity-70" />
      <span className="text-xs font-medium opacity-80">{label}</span>
    </div>
  )
}

/**
 * The frame ingest captured for one Project, or a placeholder when there is none.
 *
 * The capability is signed only once the card is on screen and lives five minutes; a frame
 * that fails to load falls back to the placeholder rather than a broken picture.
 */
export function ProjectThumbnail({
  workspaceId,
  projectId,
  placeholder = 'No preview yet',
  hasMedia = true,
}: {
  workspaceId: string
  projectId: string
  placeholder?: string
  /** False for a Project still waiting for its video, which has no frame to show. */
  hasMedia?: boolean
}) {
  const [broken, setBroken] = useState(false)
  const [visible, setVisible] = useState(false)
  const frame = useRef<HTMLDivElement>(null)

  // Each card signs its own frame, so only cards on screen ask: a long grid must not
  // spend the member's request allowance on pictures nobody has scrolled to.
  useEffect(() => {
    const element = frame.current
    if (element === null || visible) {
      return
    }
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true)
      return
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true)
        observer.disconnect()
      }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [visible])

  const thumbnail = useQuery<MediaPreviewResponse, ApiError>({
    queryKey: ['/api/v1/projects/thumbnail', workspaceId, projectId],
    queryFn: ({ signal }) =>
      thumbnailApiV1ProjectsProjectIdThumbnailGet(
        projectId,
        { workspace_id: workspaceId },
        { signal },
      ),
    retry: false,
    enabled: hasMedia && visible,
    staleTime: 4 * 60_000,
  })

  return (
    <div ref={frame} className="size-full">
      {thumbnail.data === undefined || broken ? (
        <MediaPlaceholder label={placeholder} />
      ) : (
        // Signed object-store URLs are not known to the Next image optimizer.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={thumbnail.data.url}
          alt=""
          loading="lazy"
          onError={() => setBroken(true)}
          className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.02]"
        />
      )}
    </div>
  )
}
