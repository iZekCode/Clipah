'use client'

import { useState } from 'react'

import type { SoundTrackKind } from './store'
import { Checkbox } from '@/components/ui/checkbox'
import type { CompositionV1 } from '@/lib/api/generated/model'

/** How each lane describes itself in the panel. */
const LANE_NAMES: Record<string, string> = {
  video: 'Picture',
  audio: 'Sound',
  music: 'Music',
  extractedAudio: 'Extracted audio',
}

/**
 * The two levels a version 1 composition sets, and the lanes they apply to.
 *
 * Levels belong to the composition rather than to an item, because that is what the
 * renderer reads: the dialogue gain covers the clip's own speech and any audio a member
 * extracted from picture, and the music gain covers the beds under it.
 */
export function AudioPanel({
  composition,
  lockedTrackIds,
  onAudio,
  onAddTrack,
  onToggleLock,
}: {
  composition: CompositionV1
  lockedTrackIds: string[]
  onAudio: (patch: Partial<CompositionV1['audio']>) => void
  onAddTrack: (kind: SoundTrackKind) => void
  onToggleLock: (trackId: string) => void
}) {
  // A level is committed when a member leaves the field. Committing every keystroke
  // would drop the minus sign of "-6" before the six was typed.
  const [drafts, setDrafts] = useState<{ gainDb?: string; musicGainDb?: string }>({})

  /** Send one level, then let the composition own the field again. */
  function commit(field: 'gainDb' | 'musicGainDb'): void {
    const draft = drafts[field]
    setDrafts((current) => ({ ...current, [field]: undefined }))
    const level = Number(draft)
    if (draft !== undefined && draft !== '' && Number.isFinite(level)) {
      onAudio({ [field]: level })
    }
  }

  return (
    <section aria-label="Sound" className="space-y-3 text-small">
      <h2 className="text-title">Sound</h2>

      <label className="flex items-center justify-between gap-2">
        Dialogue level (dB)
        <input
          type="number"
          aria-label="Dialogue level"
          min={-60}
          max={12}
          step={1}
          value={drafts.gainDb ?? composition.audio.gainDb}
          onChange={(event) => {
            const value = event.currentTarget.value
            setDrafts((current) => ({ ...current, gainDb: value }))
          }}
          onBlur={() => commit('gainDb')}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              commit('gainDb')
            }
          }}
          className="w-24 h-8 rounded-md border border-input bg-secondary px-2 font-mono text-small text-foreground"
        />
      </label>
      <label className="flex items-center justify-between gap-2">
        Music level (dB)
        <input
          type="number"
          aria-label="Music level"
          min={-60}
          max={12}
          step={1}
          value={drafts.musicGainDb ?? composition.audio.musicGainDb}
          onChange={(event) => {
            const value = event.currentTarget.value
            setDrafts((current) => ({ ...current, musicGainDb: value }))
          }}
          onBlur={() => commit('musicGainDb')}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              commit('musicGainDb')
            }
          }}
          className="w-24 h-8 rounded-md border border-input bg-secondary px-2 font-mono text-small text-foreground"
        />
      </label>

      <ul className="flex flex-col gap-1">
        {composition.tracks.map((track) => (
          <li key={track.id} className="flex items-center gap-2 py-2">
            <span>{LANE_NAMES[track.type] ?? track.type}</span>
            <span className="font-mono text-muted-foreground">{track.id}</span>
            <span className="text-muted-foreground">{track.items.length} items</span>
            <label className="ml-auto flex items-center gap-1">
              <Checkbox
                aria-label={`Lock ${track.id}`}
                checked={lockedTrackIds.includes(track.id)}
                onChange={() => onToggleLock(track.id)}
              />
              Locked
            </label>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => onAddTrack('music')} className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40">
          New music lane
        </button>
        <button
          type="button"
          onClick={() => onAddTrack('extractedAudio')}
          className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40"
        >
          New extracted audio lane
        </button>
      </div>
    </section>
  )
}
