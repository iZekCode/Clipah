import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { Inspector } from '@/features/editor/Inspector'

import { composition } from './support/fixtures'

function handlers() {
  return {
    onTrim: vi.fn(),
    onCrop: vi.fn(),
    onSplit: vi.fn(),
    onDelete: vi.fn(),
    onRetimeWord: vi.fn(),
    onWordText: vi.fn(),
    onMoveOverlay: vi.fn(),
  }
}

describe('Inspector', () => {
  test('with nothing selected it describes the canvas and how to select', () => {
    render(<Inspector composition={composition()} target={null} playheadMs={0} {...handlers()} />)

    const inspector = screen.getByRole('region', { name: 'Inspector' })
    expect(inspector).toHaveTextContent('1080 × 1920')
    expect(inspector).toHaveTextContent(
      'Select an item on the timeline, a caption word, or a text overlay.',
    )
  })

  test('a selected item is trimmed with timecodes', async () => {
    const user = userEvent.setup()
    const actions = handlers()
    render(
      <Inspector
        composition={composition()}
        target={{ kind: 'item', id: 'scene-1' }}
        playheadMs={0}
        {...actions}
      />,
    )

    expect(screen.getByRole('textbox', { name: 'Start' })).toHaveValue('0:01.00')
    const end = screen.getByRole('textbox', { name: 'End' })
    expect(end).toHaveValue('0:31.00')
    expect(screen.getByText('Duration 0:30.00')).toBeInTheDocument()
    await user.clear(end)
    await user.type(end, '0:21.00{Enter}')

    expect(actions.onTrim).toHaveBeenCalledWith(1_000, 21_000)
  })

  test('a selected caption word is retimed and retyped', async () => {
    const user = userEvent.setup()
    const actions = handlers()
    render(
      <Inspector
        composition={composition()}
        target={{ kind: 'word', id: 'w000002' }}
        playheadMs={0}
        {...actions}
      />,
    )

    await user.clear(screen.getByRole('textbox', { name: 'Word' }))
    await user.type(screen.getByRole('textbox', { name: 'Word' }), 'karya{Enter}')
    await user.clear(screen.getByRole('textbox', { name: 'Start' }))
    await user.type(screen.getByRole('textbox', { name: 'Start' }), '0:01.20{Enter}')

    expect(actions.onWordText).toHaveBeenCalledWith('w000002', 'karya')
    expect(actions.onRetimeWord).toHaveBeenCalledWith('w000002', 1_200, 1_900)
  })
})
