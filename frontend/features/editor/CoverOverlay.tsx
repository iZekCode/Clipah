'use client'

import type { CSSProperties } from 'react'

import { captionFontStack } from './caption-fonts'
import type { CompositionCover } from './store'

type Preset = CompositionCover['preset']

/**
 * How each design writes its title, every length a share of the frame width — the same
 * numbers the worker draws the picture with, so the preview is the picture.
 */
const TITLES: Record<Exclude<Preset, 'minimal'>, CSSProperties> = {
  bold: {
    fontFamily: captionFontStack('Poppins'),
    fontWeight: 800,
    fontSize: 'calc(8.5cqw)',
    color: '#FFD166',
    textTransform: 'uppercase',
    WebkitTextStroke: 'calc(1.2cqw) #000000',
    paintOrder: 'stroke fill',
    WebkitLineClamp: 4,
  },
  clean: {
    fontFamily: captionFontStack('Poppins'),
    fontWeight: 600,
    fontSize: 'calc(6.5cqw)',
    color: '#FFFFFF',
    WebkitLineClamp: 3,
  },
  topTitle: {
    fontFamily: captionFontStack('Anton'),
    fontWeight: 400,
    fontSize: 'calc(10cqw)',
    color: '#FFFFFF',
    textTransform: 'uppercase',
    WebkitLineClamp: 3,
  },
}

/**
 * A clip's cover design drawn over the frame the player shows: the shade, the band, and
 * the title where the picture will have them. A minimal cover draws nothing over the frame.
 */
export function CoverOverlay({ cover }: { cover: CompositionCover }) {
  if (cover.preset === 'minimal') return null
  const title = cover.title ?? ''
  const text = (
    <span
      data-testid="cover-title"
      style={{ ...TITLES[cover.preset], lineHeight: 1.12, display: '-webkit-box', WebkitBoxOrient: 'vertical' }}
      className="overflow-hidden text-center [overflow-wrap:anywhere]"
    >
      {title}
    </span>
  )
  return (
    <div data-testid="cover-overlay" data-preset={cover.preset} className="pointer-events-none absolute inset-0">
      {cover.preset === 'bold' ? (
        <>
          <div
            className="absolute inset-x-0 bottom-0 top-[55%]"
            style={{ background: 'linear-gradient(to bottom, rgb(0 0 0 / 0), rgb(0 0 0 / 0.85))' }}
          />
          <div className="absolute inset-x-[8%] bottom-[14%] flex justify-center">{text}</div>
        </>
      ) : null}
      {cover.preset === 'topTitle' ? (
        <>
          <div
            className="absolute inset-x-0 top-0 h-[45%]"
            style={{ background: 'linear-gradient(to bottom, rgb(0 0 0 / 0.8), rgb(0 0 0 / 0))' }}
          />
          <div className="absolute inset-x-[8%] top-[10%] flex justify-center">{text}</div>
        </>
      ) : null}
      {cover.preset === 'clean' ? (
        <div
          className="absolute inset-x-0 top-[72%] flex -translate-y-1/2 justify-center bg-black/70 px-[8%]"
          style={{ paddingBlock: 'calc(4cqw)' }}
        >
          {text}
        </div>
      ) : null}
    </div>
  )
}
