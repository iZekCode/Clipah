/**
 * The looks and movements the backend published, read rather than written down again.
 *
 * `templates.generated.json` is exported from `backend/src/clipah/renders/templates.py`,
 * so the editor offers exactly what the renderer enforces. Nothing here is edited by
 * hand: a look the browser knew about and the renderer did not would be a look a member
 * could apply and never export.
 */
import published from './templates.generated.json'

import type { CompositionV1 } from '@/lib/api/generated/model'

type CaptionStyle = CompositionV1['captions']['style']
type CaptionMode = CompositionV1['captions']['mode']
type TextOverlay = Extract<CompositionV1['overlays'][number], { type: 'text' }>
type MotionPreset = TextOverlay['motion']

/** One complete look, at one immutable version. */
export interface TemplateDefinition {
  id: string
  version: number
  name: string
  description: string
  captionMode: CaptionMode
  captionStyle: CaptionStyle
  textStyle: TextOverlay['style']
}

/** One named movement and the window in which it reads as that movement. */
export interface MotionDefinition {
  preset: MotionPreset
  minDurationMs: number
  maxDurationMs: number
  description: string
}

/** Every look a member may apply, in the order the backend published them. */
export const TEMPLATES: TemplateDefinition[] = published.templates as TemplateDefinition[]

/** Every movement an element may be given, with the window it is legible within. */
export const MOTIONS: MotionDefinition[] = published.motions as MotionDefinition[]

/** Report the window one movement is legible within. */
export function motionDefinition(preset: MotionPreset): MotionDefinition {
  const definition = MOTIONS.find((entry) => entry.preset === preset)
  if (definition === undefined) {
    throw new Error(`no published definition for the movement ${preset}`)
  }
  return definition
}

/** Whether one element is on screen long enough for one movement to read. */
export function motionFits(preset: MotionPreset, durationMs: number): boolean {
  const definition = motionDefinition(preset)
  return durationMs >= definition.minDurationMs && durationMs <= definition.maxDurationMs
}
