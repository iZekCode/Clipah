import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { CaptionsPanel } from '@/features/editor/CaptionsPanel'

import { composition } from './support/fixtures'

function renderPanel(overrides: Partial<Parameters<typeof CaptionsPanel>[0]> = {}) {
  const actions = { onText: vi.fn(), onSeek: vi.fn(), onSelectWord: vi.fn() }
  render(
    <CaptionsPanel
      captions={composition().captions}
      playheadMs={1_200}
      selectedWordId={null}
      timing={<p>Timing body</p>}
      {...actions}
      {...overrides}
    />,
  )
  return actions
}

describe('CaptionsPanel', () => {
  test('reads as text, lights the word being said, and seeks when a word is clicked', async () => {
    const user = userEvent.setup()
    const actions = renderPanel()

    const panel = screen.getByRole('region', { name: 'Captions' })
    const words = within(panel).getAllByRole('button', { name: /^word at/i })
    expect(words.map((word) => word.textContent)).toEqual(['Ini', 'cara', 'kerja', 'editornya'])
    expect(within(panel).getByRole('button', { name: 'Word at 0:01.00' })).toHaveAttribute(
      'aria-current',
      'true',
    )

    await user.click(within(panel).getByRole('button', { name: 'Word at 0:12.00' }))
    expect(actions.onSeek).toHaveBeenCalledWith(12_000)
    expect(actions.onSelectWord).toHaveBeenCalledWith('w000003')
  })

  test('double-click edits a word in place without moving its timing', async () => {
    const user = userEvent.setup()
    const actions = renderPanel()

    await user.dblClick(screen.getByRole('button', { name: 'Word at 0:01.00' }))
    const field = screen.getByRole('textbox', { name: 'Word at 0:01.00' })
    expect(field).toHaveFocus()
    await user.clear(field)
    await user.type(field, 'karya{Enter}')

    expect(actions.onText).toHaveBeenCalledWith('w000002', 'karya')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  test('Escape abandons an edit, and an empty word is never committed', async () => {
    const user = userEvent.setup()
    const actions = renderPanel()

    await user.dblClick(screen.getByRole('button', { name: 'Word at 0:00.00' }))
    await user.clear(screen.getByRole('textbox', { name: 'Word at 0:00.00' }))
    await user.keyboard('{Enter}')
    await user.dblClick(screen.getByRole('button', { name: 'Word at 0:00.00' }))
    await user.type(screen.getByRole('textbox', { name: 'Word at 0:00.00' }), 'x{Escape}')

    expect(actions.onText).not.toHaveBeenCalled()
  })

  test('Timing view shows the karaoke controls', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(screen.getByRole('button', { name: 'Timing' }))

    expect(screen.getByText('Timing body')).toBeInTheDocument()
  })
})
