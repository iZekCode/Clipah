'use client'

import { useReducedMotion } from '@/features/media/use-reduced-motion'
import { cn } from '@/lib/utils'

/** Real product footage: a muted loop when motion is welcome, otherwise the still. */
export function MarketingFrame({
  still,
  video,
  alt,
  className,
}: {
  still: string
  video?: { webm: string; mp4: string }
  alt: string
  className?: string
}) {
  const reduced = useReducedMotion()
  return (
    <div className={cn('overflow-hidden rounded-lg border border-line-strong bg-stage', className)}>
      {video === undefined || reduced ? (
        // Static marketing stills are served from /public and need no optimizer.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={still} alt={alt} className="block w-full" />
      ) : (
        <video autoPlay muted loop playsInline poster={still} aria-label={alt} className="block w-full">
          <source src={video.webm} type="video/webm" />
          <source src={video.mp4} type="video/mp4" />
        </video>
      )}
    </div>
  )
}
