import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { SwatchPicker } from '@/components/ui/swatch-picker'

beforeEach(() => {
  window.localStorage.clear()
})

describe('SwatchPicker', () => {
  test('offers brand colours first and marks the current one', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SwatchPicker
        label="Colour"
        accessibleName="Caption colour"
        value="#FFFFFF"
        onChange={onChange}
        brandColors={[
          { name: 'Paper', hex: '#FFFFFF' },
          { name: 'Signal lime', hex: '#C6FF3D' },
        ]}
      />,
    )

    const group = screen.getByRole('group', { name: 'Caption colour' })
    expect(within(group).getByRole('button', { name: 'Paper #FFFFFF' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(group).getByText('#FFFFFF')).toBeInTheDocument()
    await user.click(within(group).getByRole('button', { name: 'Signal lime #C6FF3D' }))

    expect(onChange).toHaveBeenCalledWith('#C6FF3D')
  })

  test('a custom hex is validated, applied, and remembered as recent', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SwatchPicker
        label="Colour"
        accessibleName="Caption colour"
        value="#FFFFFF"
        onChange={onChange}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Custom colour' }))
    const hex = await screen.findByRole('textbox', { name: 'Hex colour' })
    await user.clear(hex)
    await user.type(hex, '12ab{Enter}')
    expect(screen.getByRole('alert')).toHaveTextContent('Use six hex digits, for example #FFB020')

    await user.clear(hex)
    await user.type(hex, '#ffb020{Enter}')

    expect(onChange).toHaveBeenCalledWith('#FFB020')
    expect(JSON.parse(window.localStorage.getItem('clipah.recent-colours') ?? '[]')).toEqual([
      '#FFB020',
    ])
  })
})
