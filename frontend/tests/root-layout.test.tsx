import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test, vi } from 'vitest'

vi.mock('@/lib/design/fonts', () => ({ fontVariables: 'font-sans-var font-mono-var' }))

import RootLayout, { metadata } from '@/app/layout'

describe('the root layout', () => {
  test('puts both Signal font variables on the document and keeps English as the language', () => {
    const markup = renderToStaticMarkup(
      <RootLayout>
        <p>Studio</p>
      </RootLayout>,
    )

    expect(markup).toContain('<html lang="en" class="font-sans-var font-mono-var">')
    expect(markup).toContain('<p>Studio</p>')
  })

  test('describes the product in plain words', () => {
    expect(metadata.description).toBe('Turn one long video into short clips worth posting.')
  })
})
