import type { StoryboardResponse } from '@/lib/api/generated/model'
import { tilesAcross } from '@/lib/media/storyboard'
import { cn } from '@/lib/utils'

import { SpriteFrame } from './sprite-frame'

/** Frames of a range laid side by side, the way a timeline shows what is on each lane. */
export function Filmstrip({
  storyboard,
  startMs,
  endMs,
  tileCount,
  className,
}: {
  storyboard: StoryboardResponse | null
  startMs: number
  endMs: number
  tileCount: number
  className?: string
}) {
  const tiles =
    storyboard === null || tileCount <= 0 ? [] : tilesAcross(storyboard, startMs, endMs, tileCount)
  return (
    <div aria-hidden="true" className={cn('flex overflow-hidden bg-stage', className)}>
      {tiles.map((tile, index) => (
        <div
          key={index}
          className="relative h-full min-w-0 flex-1 border-r border-background/60 last:border-r-0"
        >
          <SpriteFrame tile={tile} containerAspect={tile.tileWidth / tile.tileHeight} />
        </div>
      ))}
    </div>
  )
}
