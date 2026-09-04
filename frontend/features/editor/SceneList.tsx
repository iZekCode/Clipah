'use client'

import { useState } from 'react'

import { timecode } from './Player'
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
    <section aria-label="Scenes" className="flex flex-col gap-2 rounded-lg border p-3">
      <h2 className="text-sm font-medium">Scenes</h2>
      {list.length === 0 ? (
        <p className="text-xs text-muted-foreground">This clip has no transcript words yet.</p>
      ) : null}
      <ol className="flex flex-col gap-2">
        {list.map((scene) => (
          <li key={scene.id} className="flex flex-col gap-1 rounded border p-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="font-mono text-muted-foreground">
                {timecode(scene.startMs)}–{timecode(scene.endMs)}
              </span>
              <span>{scene.speaker ?? 'Unknown speaker'}</span>
              <span className="text-muted-foreground">{scene.wordCount} words</span>
              <button
                type="button"
                onClick={() => onSeek(scene.startMs)}
                className="ml-auto rounded border px-2 py-1"
              >
                Go to
              </button>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="text"
                aria-label={`Label the scene at ${timecode(scene.startMs)}`}
                value={drafts[scene.id] ?? scene.label ?? ''}
                onChange={(event) => {
                  const value = event.currentTarget.value
                  setDrafts((current) => ({ ...current, [scene.id]: value }))
                }}
                className="flex-1 rounded border px-2 py-1"
              />
              <button
                type="button"
                onClick={() => {
                  const label = drafts[scene.id] ?? scene.label ?? ''
                  if (label.trim() !== '') {
                    onLabel(scene.startMs, label)
                  }
                }}
                className="rounded border px-2 py-1"
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
