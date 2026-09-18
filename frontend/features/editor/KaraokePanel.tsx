'use client'

import { SegmentedControl } from '@/components/ui/segmented-control'
import { TimecodeInput } from '@/components/ui/timecode-input'
import type { CompositionV1 } from '@/lib/api/generated/model'
import { cn } from '@/lib/utils'

import { activeWordAt } from './store'

type CaptionMode = CompositionV1['captions']['mode']

/**
 * Caption word timing, and the word karaoke is painting right now.
 *
 * Transcription produced these timestamps, so nothing here changes on its own: a word
 * moves only when a member types a new time and leaves the field. Every retime is bounded
 * by the words on either side, because two words claiming one instant would give karaoke
 * two active words at once — and the backend would refuse the save.
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
  const active = activeWordAt(captions.words, playheadMs)

  return (
    <section aria-label="Karaoke" className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-small font-semibold">Karaoke</h3>
        <SegmentedControl<CaptionMode>
          label="Caption mode"
          size="sm"
          value={captions.mode}
          options={[
            { value: 'off', label: 'Off' },
            { value: 'block', label: 'Block' },
            { value: 'karaoke', label: 'Karaoke' },
          ]}
          onChange={onMode}
        />
      </div>

      <p role="status" className="text-caption text-muted-foreground">
        {active === null ? 'No word is being said here.' : `Now saying: ${active.text}`}
      </p>

      <ol className="divide-y divide-border">
        {captions.words.map((word) => (
          <li key={word.id} className="grid grid-cols-[minmax(0,1fr)_6rem_6rem] items-center gap-2 py-1.5">
            <span
              className={cn(
                'truncate text-small',
                active?.id === word.id ? 'font-semibold text-foreground' : 'text-muted-foreground',
              )}
            >
              {word.text}
            </span>
            <TimecodeInput
              label="From"
              hideLabel
              accessibleName={`Start of ${word.id}`}
              valueMs={word.startMs}
              onCommit={(ms) => onRetime(word.id, ms, word.endMs)}
            />
            <TimecodeInput
              label="To"
              hideLabel
              accessibleName={`End of ${word.id}`}
              valueMs={word.endMs}
              onCommit={(ms) => onRetime(word.id, word.startMs, ms)}
            />
          </li>
        ))}
      </ol>
    </section>
  )
}
