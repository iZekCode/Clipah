import { describe, expect, test } from 'vitest'

import {
  CAPTION_FONT_FAMILIES,
  CAPTION_FONT_VARIABLES,
  captionFontStack,
} from '@/features/editor/caption-fonts'

describe('caption fonts', () => {
  test('every family the composition allows has a vendored face and a CSS variable', () => {
    expect(CAPTION_FONT_FAMILIES).toEqual([
      'Inter',
      'Montserrat',
      'Poppins',
      'Roboto',
      'Open Sans',
      'Bebas Neue',
      'Anton',
      'Nunito',
    ])
    expect(captionFontStack('Bebas Neue')).toBe('var(--caption-font-bebas-neue), sans-serif')
    expect(CAPTION_FONT_VARIABLES.split(' ')).toHaveLength(8)
  })
})
