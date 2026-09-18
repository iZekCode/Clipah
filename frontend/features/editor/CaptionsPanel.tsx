'use client'

import { useRef, useState, type ReactNode } from 'react'

import { SegmentedControl } from '@/components/ui/segmented-control'
import type { CompositionV1 } from '@/lib/api/generated/model'
import { formatTimecode } from '@/lib/time/timecode'
import { cn } from '@/lib/utils'

import { activeWordAt } from './store'

type CaptionWord = CompositionV1['captions']['words'][number]

/**
 * Captions as the words they are.
 *
 * The word under the playhead is lit, clicking a word jumps to it, and double-clicking (or
 * Enter) edits its text in place. Timing never changes here: transcription measured it, and
 * retiming is the Timing view's explicit job.
 */
export function CaptionsPanel({
  captions,
  playheadMs,
  selectedWordId,
  onText,
  onSeek,
  onSelectWord,
  timing,
}: {
  captions: CompositionV1['captions']
  playheadMs: number
  selectedWordId: string | null
  onText: (wordId: string, text: string) => void
  onSeek: (ms: number) => void
  onSelectWord: (wordId: string) => void
  timing: ReactNode
}) {
  const [view, setView] = useState<'words' | 'timing'>('words')
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  // Enter and Escape finish an edit themselves; the blur that can follow when the field
  // unmounts must not commit a second time or undo an Escape.
  const settled = useRef(false)
  const active = activeWordAt(captions.words, playheadMs)

  function start(word: CaptionWord): void {
    settled.current = false
    setEditing(word.id)
    setDraft(word.text)
  }

  function commit(word: CaptionWord): void {
    if (settled.current) return
    settled.current = true
    setEditing(null)
    const text = draft.trim()
    if (text !== '' && text !== word.text) onText(word.id, text)
  }

  function cancel(): void {
    settled.current = true
    setEditing(null)
  }

  return (
    <section aria-label="Captions" className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-title">Captions</h2>
        <SegmentedControl
          label="Captions view"
          size="sm"
          value={view}
          options={[
            { value: 'words', label: 'Words' },
            { value: 'timing', label: 'Timing' },
          ]}
          onChange={setView}
        />
      </div>
      {view === 'timing' ? (
        timing
      ) : (
        <p className="flex flex-wrap gap-x-1 gap-y-1.5 text-body leading-relaxed">
          {captions.words.map((word) => {
            const name = `Word at ${formatTimecode(word.startMs)}`
            if (editing === word.id) {
              return (
                <input
                  key={word.id}
                  type="text"
                  aria-label={name}
                  // Editing begins because the member asked for it on this very word.
                  autoFocus
                  value={draft}
                  size={Math.max(2, draft.length)}
                  onChange={(event) => setDraft(event.currentTarget.value)}
                  onBlur={() => commit(word)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      commit(word)
                    } else if (event.key === 'Escape') {
                      event.preventDefault()
                      cancel()
                    }
                  }}
                  className="rounded-sm border border-primary bg-secondary px-1 text-body text-foreground"
                />
              )
            }
            return (
              <button
                key={word.id}
                type="button"
                aria-label={name}
                aria-current={active?.id === word.id ? 'true' : undefined}
                aria-pressed={selectedWordId === word.id}
                onClick={() => {
                  onSeek(word.startMs)
                  onSelectWord(word.id)
                }}
                onDoubleClick={() => start(word)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === 'F2') {
                    event.preventDefault()
                    start(word)
                  }
                }}
                className={cn(
                  'rounded-sm px-0.5 transition-colors duration-fast ease-signal',
                  active?.id === word.id
                    ? 'bg-primary text-primary-foreground'
                    : 'text-foreground hover:bg-secondary',
                  selectedWordId === word.id &&
                    active?.id !== word.id &&
                    'outline outline-1 outline-primary',
                )}
              >
                {word.text}
              </button>
            )
          })}
        </p>
      )}
    </section>
  )
}
