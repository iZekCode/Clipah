'use client'

import { useState } from 'react'

import { timecode } from './Player'
import type { CompositionV1 } from '@/lib/api/generated/model'

/** The fonts a composition may name, offered in the order the Brand Kit will replace. */
const FONTS: Array<CompositionV1['captions']['style']['fontFamily']> = [
  'Inter',
  'Montserrat',
  'Poppins',
  'Roboto',
  'Open Sans',
  'Bebas Neue',
  'Anton',
  'Nunito',
]

/**
 * Caption words and the type they are drawn in.
 *
 * A word's text is editable and its timing is not: transcription produced those
 * timestamps, and karaoke highlighting is only honest while they still describe when the
 * word was said. Retiming a word is an explicit operation the advanced editor owns.
 */
export function CaptionsPanel({
  captions,
  onText,
  onStyle,
}: {
  captions: CompositionV1['captions']
  onText: (wordId: string, text: string) => void
  onStyle: (patch: Partial<CompositionV1['captions']['style']>) => void
}) {
  // What a member has typed but not yet finished. A word is committed when they leave
  // the field, so a half-typed word never becomes a Revision and an empty one never
  // becomes a caption.
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  /** Commit one word, or put back the text that is still in the composition. */
  function commit(wordId: string): void {
    const draft = drafts[wordId]
    if (draft !== undefined) {
      onText(wordId, draft)
    }
    setDrafts((current) =>
      Object.fromEntries(Object.entries(current).filter(([key]) => key !== wordId)),
    )
  }

  return (
    <section aria-label="Captions" className="flex flex-col gap-3 rounded-lg border p-3">
      <h2 className="text-sm font-medium">Captions</h2>

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1">
          Font
          <select
            aria-label="Caption font"
            value={captions.style.fontFamily}
            onChange={(event) =>
              onStyle({
                fontFamily: event.currentTarget
                  .value as CompositionV1['captions']['style']['fontFamily'],
              })
            }
            className="rounded border px-1 py-0.5"
          >
            {FONTS.map((font) => (
              <option key={font} value={font}>
                {font}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          Size
          <input
            type="number"
            aria-label="Caption size"
            min={12}
            max={200}
            value={captions.style.fontSize}
            onChange={(event) => onStyle({ fontSize: Number(event.currentTarget.value) })}
            className="w-20 rounded border px-1 py-0.5"
          />
        </label>
        <label className="flex items-center gap-1">
          Colour
          <input
            type="text"
            aria-label="Caption colour"
            value={captions.style.color}
            onChange={(event) => onStyle({ color: event.currentTarget.value })}
            className="w-24 rounded border px-1 py-0.5"
          />
        </label>
        <label className="flex items-center gap-1">
          Alignment
          <select
            aria-label="Caption alignment"
            value={captions.style.align}
            onChange={(event) =>
              onStyle({
                align: event.currentTarget.value as CompositionV1['captions']['style']['align'],
              })
            }
            className="rounded border px-1 py-0.5"
          >
            <option value="left">Left</option>
            <option value="center">Centre</option>
            <option value="right">Right</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            aria-label="Caption background"
            checked={captions.style.backgroundEnabled}
            onChange={(event) => onStyle({ backgroundEnabled: event.currentTarget.checked })}
          />
          Background
        </label>
      </div>

      <ol className="flex flex-col gap-1">
        {captions.words.map((word) => (
          <li key={word.id} className="flex items-center gap-2 text-xs">
            <span className="w-16 font-mono text-muted-foreground">{timecode(word.startMs)}</span>
            <input
              type="text"
              aria-label={`Word at ${timecode(word.startMs)}`}
              value={drafts[word.id] ?? word.text}
              onChange={(event) => {
                const text = event.currentTarget.value
                setDrafts((current) => ({ ...current, [word.id]: text }))
              }}
              onBlur={() => commit(word.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  commit(word.id)
                }
              }}
              className="flex-1 rounded border px-2 py-1"
            />
          </li>
        ))}
      </ol>
    </section>
  )
}
