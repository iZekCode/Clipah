import { spriteStyle, type StoryboardTile } from '@/lib/media/storyboard'

/**
 * One storyboard tile, cover-cropped into its box.
 *
 * The inner element keeps the tile's own shape and is centred over a box of any shape, so a
 * landscape frame fills a 9:16 poster by cropping its sides rather than stretching.
 */
export function SpriteFrame({
  tile,
  containerAspect,
}: {
  tile: StoryboardTile
  containerAspect: number
}) {
  const tileAspect = tile.tileWidth / tile.tileHeight
  const fillHeight = tileAspect > containerAspect
  return (
    <div className="absolute inset-0 overflow-hidden bg-stage">
      <div
        data-testid="sprite"
        className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-no-repeat"
        style={{
          ...(fillHeight ? { height: '100%' } : { width: '100%' }),
          aspectRatio: `${tile.tileWidth} / ${tile.tileHeight}`,
          ...spriteStyle(tile),
        }}
      />
    </div>
  )
}
