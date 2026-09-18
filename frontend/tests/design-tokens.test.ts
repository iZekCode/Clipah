import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, test } from 'vitest'

import {
  BOUNDARY_PAIRS,
  SIGNAL_TOKENS,
  SOLID_PAIRS,
  TEXT_ON_SURFACE_PAIRS,
  contrastRatio,
  hslSaturationAndHue,
  parseTokenTriplets,
  type SignalToken,
} from '@/lib/design/tokens'
import { cn } from '@/lib/utils'

const css = readFileSync(resolve(__dirname, '../app/globals.css'), 'utf8')

describe('the Signal palette', () => {
  test('globals.css stores exactly the hex values the design system declares', () => {
    const stored = parseTokenTriplets(css)
    for (const [name, hex] of Object.entries(SIGNAL_TOKENS)) {
      expect(stored[name], `--${name}`).toBe(hex)
    }
  })

  test('every text colour reads at 4.5:1 on every surface it is placed on', () => {
    for (const [text, surface] of TEXT_ON_SURFACE_PAIRS) {
      expect(ratio(text, surface), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5)
    }
  })

  test('text on a filled or tinted colour reads at 4.5:1', () => {
    for (const [text, fill] of SOLID_PAIRS) {
      expect(ratio(text, fill), `${text} on ${fill}`).toBeGreaterThanOrEqual(4.5)
    }
  })

  test('control borders are visible at 3:1 on every surface', () => {
    for (const [border, surface] of BOUNDARY_PAIRS) {
      expect(ratio(border, surface), `${border} on ${surface}`).toBeGreaterThanOrEqual(3)
    }
  })

  test('no chromatic token is violet, indigo, or purple', () => {
    for (const [name, hex] of Object.entries(SIGNAL_TOKENS)) {
      const { hue, saturation } = hslSaturationAndHue(hex)
      const banned = saturation >= 20 && hue >= 240 && hue <= 300
      expect(banned, `--${name} ${hex}`).toBe(false)
    }
  })

  test('the type scale is registered with tailwind-merge so size and colour both survive', () => {
    expect(cn('text-caption', 'text-muted-foreground')).toBe('text-caption text-muted-foreground')
    expect(cn('text-small', 'text-h1')).toBe('text-h1')
  })
})

function ratio(foreground: SignalToken, background: SignalToken): number {
  return contrastRatio(SIGNAL_TOKENS[foreground], SIGNAL_TOKENS[background])
}
