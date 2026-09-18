'use client'

import { formatTimecode } from '@/lib/time/timecode'
import { useState } from 'react'

import { scenes } from './store'
import type { CompositionV1 } from '@/lib/api/generated/model'

/**
 * The clip as scenes, one per stretch of a single speaker.
 *
 * Nothing here is stored twice: the scenes are read out of the caption words the
 * transcript produced, and naming one leaves a marker on the timeline rather than a
 * second, private idea of where a scene begins. Speaker labels are the provider's own
 * opaque identifiers, rendered as text and never as markup.
 */
export function SceneList({
  composition,
  onSeek,
  onLabel,
}: {
  composition: CompositionV1
  onSeek: (ms: number) => void
  onLabel: (atMs: number, label: string) => void
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const list = scenes(composition)

  return (
    <section aria-label="Scenes" className="space-y-3">
      <h2 className="text-title">Scenes</h2>
      {list.length === 0 ? (
        <p className="text-caption text-muted-foreground">This clip has no transcript words yet.</p>
      ) : null}
      <ol className="flex flex-col gap-2">
        {list.map((scene) => (
          <li key={scene.id} className="flex flex-col gap-1.5 py-2 text-small">
            <div className="flex items-center gap-2">
              <span className="font-mono text-muted-foreground">
                {formatTimecode(scene.startMs)}–{formatTimecode(scene.endMs)}
              </span>
              <span>{scene.speaker ?? 'Unknown speaker'}</span>
              <span className="text-muted-foreground">{scene.wordCount} words</span>
              <button
                type="button"
                onClick={() => onSeek(scene.startMs)}
                className="ml-auto inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40"
              >
                Go to
              </button>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="text"
                aria-label={`Label the scene at ${formatTimecode(scene.startMs)}`}
                value={drafts[scene.id] ?? scene.label ?? ''}
                onChange={(event) => {
                  const value = event.currentTarget.value
                  setDrafts((current) => ({ ...current, [scene.id]: value }))
                }}
                className="flex-1 rounded-lg border bg-card px-2.5 py-1"
              />
              <button
                type="button"
                onClick={() => {
                  const label = drafts[scene.id] ?? scene.label ?? ''
                  if (label.trim() !== '') {
                    onLabel(scene.startMs, label)
                  }
                }}
                className="inline-flex h-8 items-center rounded-md border border-line-strong bg-secondary px-3 text-small font-medium transition-colors duration-fast ease-signal hover:border-input disabled:opacity-40"
              >
                Label
              </button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
