'use client'

import { useState } from 'react'

import { timecode } from './Player'
import { Checkbox } from '@/components/ui/checkbox'
import { Select } from '@/components/ui/select'
import type { CompositionV1 } from '@/lib/api/generated/model'

/** The weights every shipped font face carries. */
const WEIGHTS = [300, 400, 500, 600, 700, 800, 900] as const

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
  // A half-typed measurement is not a measurement, so these two are committed when a
  // member leaves the field rather than on every keystroke.
  const [numbers, setNumbers] = useState<{ letterSpacing?: string; lineHeight?: string }>({})

  /** Send one measurement, then let the composition own the field again. */
  function commitNumber(field: 'letterSpacing' | 'lineHeight'): void {
    const draft = numbers[field]
    setNumbers((current) => ({ ...current, [field]: undefined }))
    const value = Number(draft)
    if (draft !== undefined && draft !== '' && Number.isFinite(value)) {
      onStyle({ [field]: value })
    }
  }

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
    <section aria-label="Captions" className="surface flex flex-col gap-3 p-4">
      <h2 className="text-sm font-medium">Captions</h2>

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1">
          Font
          <Select
            aria-label="Caption font"
            value={captions.style.fontFamily}
            onChange={(event) =>
              onStyle({
                fontFamily: event.currentTarget
                  .value as CompositionV1['captions']['style']['fontFamily'],
              })
            }
            controlSize="sm"
          >
            {FONTS.map((font) => (
              <option key={font} value={font}>
                {font}
              </option>
            ))}
          </Select>
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
          <Select
            aria-label="Caption alignment"
            value={captions.style.align}
            onChange={(event) =>
              onStyle({
                align: event.currentTarget.value as CompositionV1['captions']['style']['align'],
              })
            }
            controlSize="sm"
          >
            <option value="left">Left</option>
            <option value="center">Centre</option>
            <option value="right">Right</option>
          </Select>
        </label>
        <label className="flex items-center gap-1">
          <Checkbox
            aria-label="Caption background"
            checked={captions.style.backgroundEnabled}
            onChange={(event) => onStyle({ backgroundEnabled: event.currentTarget.checked })}
          />
          Background
        </label>
        <label className="flex items-center gap-1">
          Weight
          <Select
            aria-label="Caption weight"
            value={captions.style.weight}
            onChange={(event) => onStyle({ weight: Number(event.currentTarget.value) })}
            controlSize="sm"
          >
            {WEIGHTS.map((weight) => (
              <option key={weight} value={weight}>
                {weight}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex items-center gap-1">
          <Checkbox
            aria-label="Caption italic"
            checked={captions.style.italic}
            onChange={(event) => onStyle({ italic: event.currentTarget.checked })}
          />
          Italic
        </label>
        <label className="flex items-center gap-1">
          Decoration
          <Select
            aria-label="Caption decoration"
            value={captions.style.decoration}
            onChange={(event) =>
              onStyle({
                decoration: event.currentTarget
                  .value as CompositionV1['captions']['style']['decoration'],
              })
            }
            controlSize="sm"
          >
            <option value="none">None</option>
            <option value="underline">Underline</option>
            <option value="strikethrough">Strikethrough</option>
          </Select>
        </label>
        <label className="flex items-center gap-1">
          Letter spacing
          <input
            type="number"
            aria-label="Caption letter spacing"
            min={-10}
            max={40}
            step={0.5}
            value={numbers.letterSpacing ?? captions.style.letterSpacing}
            onChange={(event) => {
              const value = event.currentTarget.value
              setNumbers((current) => ({ ...current, letterSpacing: value }))
            }}
            onBlur={() => commitNumber('letterSpacing')}
            className="w-20 rounded border px-1 py-0.5"
          />
        </label>
        <label className="flex items-center gap-1">
          Line height
          <input
            type="number"
            aria-label="Caption line height"
            min={0.5}
            max={3}
            step={0.1}
            value={numbers.lineHeight ?? captions.style.lineHeight}
            onChange={(event) => {
              const value = event.currentTarget.value
              setNumbers((current) => ({ ...current, lineHeight: value }))
            }}
            onBlur={() => commitNumber('lineHeight')}
            className="w-20 rounded border px-1 py-0.5"
          />
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
              className="flex-1 rounded-lg border bg-card px-2.5 py-1"
            />
          </li>
        ))}
      </ol>
    </section>
  )
}
