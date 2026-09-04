'use client'

import { useState } from 'react'

import { timecode } from './Player'
import { activeWordAt } from './store'
import type { CompositionV1 } from '@/lib/api/generated/model'

type CaptionMode = CompositionV1['captions']['mode']

/**
 * Caption word timing, and the word karaoke is painting right now.
 *
 * Transcription produced these timestamps, so nothing here changes on its own: a word
 * moves only when a member types a new number and leaves the field. Every retime is
 * bounded by the words on either side, because two words claiming one instant would give
 * karaoke two active words at once — and the backend would refuse the save.
 */
export function KaraokePanel({
  captions,
  playheadMs,
  onRetime,
  onMode,
}: {
  captions: CompositionV1['captions']
  playheadMs: number
  onRetime: (wordId: string, startMs: number, endMs: number) => void
  onMode: (mode: CaptionMode) => void
}) {
  // A half-typed number is not a timing yet, so a word is committed when a member leaves
  // the field rather than on every keystroke.
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const active = activeWordAt(captions.words, playheadMs)

  /** Commit one edge of one word, and let the document own the field again. */
  function commit(wordId: string, edge: 'start' | 'end'): void {
    const word = captions.words.find((candidate) => candidate.id === wordId)
    const draft = drafts[`${wordId}-${edge}`]
    setDrafts((current) =>
      Object.fromEntries(Object.entries(current).filter(([key]) => key !== `${wordId}-${edge}`)),
    )
    if (word === undefined || draft === undefined || draft === '') {
      return
    }
    const value = Number(draft)
    if (!Number.isFinite(value)) {
      return
    }
    onRetime(
      wordId,
      edge === 'start' ? value : word.startMs,
      edge === 'end' ? value : word.endMs,
    )
  }

  return (
    <section aria-label="Karaoke" className="flex flex-col gap-2 rounded-lg border p-3 text-xs">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-medium">Karaoke</h2>
        <label className="ml-auto flex items-center gap-1">
          Captions
          <select
            aria-label="Caption mode"
            value={captions.mode}
            onChange={(event) => onMode(event.currentTarget.value as CaptionMode)}
            className="rounded border px-1 py-0.5"
          >
            <option value="off">Off</option>
            <option value="block">Block</option>
            <option value="karaoke">Karaoke</option>
          </select>
        </label>
      </div>

      <p role="status" className="text-muted-foreground">
        {active === null ? 'No word is being said here.' : `Now saying: ${active.text}`}
      </p>

      <ol className="flex flex-col gap-1">
        {captions.words.map((word) => (
          <li key={word.id} className="flex items-center gap-2">
            <span className={`w-20 truncate ${active?.id === word.id ? 'font-semibold' : ''}`}>
              {word.text}
            </span>
            <label className="flex items-center gap-1">
              From
              <input
                type="number"
                aria-label={`Start of ${word.id}`}
                step={50}
                value={drafts[`${word.id}-start`] ?? word.startMs}
                onChange={(event) => {
                  const value = event.currentTarget.value
                  setDrafts((current) => ({ ...current, [`${word.id}-start`]: value }))
                }}
                onBlur={() => commit(word.id, 'start')}
                className="w-24 rounded border px-2 py-1"
              />
            </label>
            <label className="flex items-center gap-1">
              To
              <input
                type="number"
                aria-label={`End of ${word.id}`}
                step={50}
                value={drafts[`${word.id}-end`] ?? word.endMs}
                onChange={(event) => {
                  const value = event.currentTarget.value
                  setDrafts((current) => ({ ...current, [`${word.id}-end`]: value }))
                }}
                onBlur={() => commit(word.id, 'end')}
                className="w-24 rounded border px-2 py-1"
              />
            </label>
            <span className="text-muted-foreground">{timecode(word.startMs)}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}
