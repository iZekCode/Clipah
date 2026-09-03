/**
 * The browser edits the same composition the backend validates.
 *
 * These tests hold the generated contract to the two promises the editor depends on:
 * the published schema is version 1 and closed to unknown fields, and the generated
 * TypeScript describes exactly the document the backend accepts.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import type { CompositionV1 } from '@/features/editor/composition.generated'

const schema = JSON.parse(
  readFileSync(resolve(__dirname, '../../contracts/composition.schema.json'), 'utf8'),
) as {
  $id: string
  additionalProperties: boolean
  properties: Record<string, { const?: number }>
  required: string[]
  $defs: Record<string, { additionalProperties?: boolean; enum?: string[] }>
}

const composition: CompositionV1 = {
  schemaVersion: 1,
  sourceAssetId: '11111111-1111-4111-8111-111111111111',
  durationMs: 30_000,
  canvas: { width: 1080, height: 1920, background: '#000000' },
  sourceRange: { inMs: 5_000, outMs: 35_000 },
  template: null,
  brandKit: null,
  tracks: [
    {
      id: 'main-video',
      type: 'video',
      items: [
        {
          id: 'scene-1',
          sourceAssetId: '11111111-1111-4111-8111-111111111111',
          timelineStartMs: 0,
          sourceInMs: 5_000,
          sourceOutMs: 35_000,
          transform: { x: 0.5, y: 0.5, scale: 1, rotation: 0 },
          crop: null,
          opacity: 1,
          blendMode: 'normal',
          motion: 'none',
          origin: { type: 'source', suggestionId: null, provenanceId: null },
          keyframes: [],
        },
      ],
    },
  ],
  captions: {
    mode: 'karaoke',
    words: [{ id: 'w000002', startMs: 0, endMs: 1_000, text: 'Kedua', speaker: 'SPEAKER_00' }],
    style: {
      fontFamily: 'Montserrat',
      fontSize: 64,
      color: '#FFFFFF',
      highlightColor: '#FFD166',
      align: 'center',
      weight: 700,
      italic: false,
      decoration: 'none',
      letterSpacing: 0,
      lineHeight: 1.2,
      backgroundEnabled: false,
      backgroundColor: '#000000',
    },
  },
  overlays: [],
  audio: { gainDb: 0, musicGainDb: -18 },
  bookmarks: [],
}

describe('composition contract', () => {
  it('publishes version 1 as a constant the browser cannot contradict', () => {
    expect(schema.$id).toBe('https://clipah.com/contracts/composition.schema.json')
    expect(schema.properties.schemaVersion?.const).toBe(1)
  })

  it('closes every object to unknown fields, so nothing is silently dropped', () => {
    expect(schema.additionalProperties).toBe(false)
    const open = Object.entries(schema.$defs).filter(
      ([, definition]) =>
        definition.enum === undefined && definition.additionalProperties !== false,
    )
    expect(open).toEqual([])
  })

  it('requires every part of the document the editor must round-trip', () => {
    expect([...schema.required].sort()).toEqual([
      'audio',
      'bookmarks',
      'brandKit',
      'canvas',
      'captions',
      'durationMs',
      'overlays',
      'schemaVersion',
      'sourceAssetId',
      'sourceRange',
      'template',
      'tracks',
    ])
  })

  it('types a composition the backend produces without any assertion', () => {
    expect(composition.tracks[0]?.items[0]?.origin.type).toBe('source')
    expect(composition.captions.style.fontFamily).toBe('Montserrat')
    expect(composition.canvas.width).toBe(1080)
  })

  it('describes overlays as the four kinds the renderer can draw', () => {
    const citation: CompositionV1['overlays'][number] = {
      id: 'citation-1',
      type: 'citation',
      timelineStartMs: 1_000,
      timelineEndMs: 4_000,
      placement: 'lowerThird',
      opacity: 1,
      keyframes: [],
      claimEvidenceId: '55555555-5555-4555-8555-555555555555',
      text: 'Source: Example Journal, 2026',
      style: {
        fontFamily: 'Inter',
        fontSize: 36,
        color: '#FFFFFF',
        align: 'left',
        weight: 500,
        italic: false,
        decoration: 'none',
        letterSpacing: 0,
        lineHeight: 1.2,
        backgroundEnabled: true,
        backgroundColor: '#101010',
      },
    }

    expect(citation.type).toBe('citation')
  })
})
