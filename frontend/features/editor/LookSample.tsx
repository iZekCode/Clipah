import type { CaptionMode, CaptionStyle } from '@/lib/api/generated/model'

import { captionFontStack } from './caption-fonts'

/** A caption look drawn on a small graphite 9:16 frame, the way a clip would carry it. */
export function LookSample({
  captionStyle: style,
  captionMode = 'karaoke',
}: {
  captionStyle: CaptionStyle
  captionMode?: CaptionMode
}) {
  return (
    <span
      aria-hidden="true"
      className="relative flex aspect-[9/16] items-end justify-center overflow-hidden rounded-sm bg-stage p-2"
    >
      {captionMode === 'off' ? (
        <span className="mb-3 font-mono text-[11px] text-subtle-foreground">No captions</span>
      ) : (
        <span
          data-testid="look-sample"
          style={{
            fontFamily: captionFontStack(style.fontFamily),
            fontWeight: style.weight,
            fontStyle: style.italic ? 'italic' : 'normal',
            color: style.color,
            textAlign: style.align,
            letterSpacing: `${style.letterSpacing / 4}px`,
            backgroundColor: style.backgroundEnabled ? style.backgroundColor : undefined,
          }}
          className="px-1 text-[15px] leading-tight"
        >
          Say it{' '}
          <span style={{ color: captionMode === 'karaoke' ? style.highlightColor : style.color }}>
            loud
          </span>
        </span>
      )}
    </span>
  )
}
