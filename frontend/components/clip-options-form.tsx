'use client'

import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

/** The render choices a Clip carries, typed the way the API expects to receive them. */
export interface ClipOptions {
  includeSubtitles: boolean
  includeWatermark: boolean
  watermarkText: string | null
}

/**
 * Collect the subtitle and watermark choices.
 *
 * The legacy UI posted these as form fields, which turned every toggle into the string
 * `"true"` or `"false"`. This form emits real booleans and omits the watermark text
 * entirely when no watermark was asked for.
 */
export function ClipOptionsForm({ onSubmit }: { onSubmit: (options: ClipOptions) => void }) {
  const [includeSubtitles, setIncludeSubtitles] = useState(false)
  const [includeWatermark, setIncludeWatermark] = useState(false)
  const [watermarkText, setWatermarkText] = useState('')

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = watermarkText.trim()
    onSubmit({
      includeSubtitles,
      includeWatermark,
      watermarkText: includeWatermark && trimmed !== '' ? trimmed : null,
    })
  }

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div className="flex items-center justify-between gap-4">
        <Label htmlFor="include-subtitles">Burn in subtitles</Label>
        <Switch
          id="include-subtitles"
          checked={includeSubtitles}
          onCheckedChange={setIncludeSubtitles}
        />
      </div>
      <div className="flex items-center justify-between gap-4">
        <Label htmlFor="include-watermark">Add a watermark</Label>
        <Switch
          id="include-watermark"
          checked={includeWatermark}
          onCheckedChange={setIncludeWatermark}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="watermark-text">Watermark text</Label>
        <Input
          id="watermark-text"
          value={watermarkText}
          disabled={!includeWatermark}
          onChange={(event) => setWatermarkText(event.target.value)}
          placeholder="@clipah"
        />
      </div>
      <Button type="submit">Save clip options</Button>
    </form>
  )
}
