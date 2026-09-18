import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { CropOverlay } from '@/features/editor/CropOverlay'
import { LayoutPanel } from '@/features/editor/LayoutPanel'

describe('CropOverlay', () => {
  test('arrow keys move the crop inside the frame', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<CropOverlay crop={{ x: 0.34, y: 0, width: 0.32, height: 1 }} onCommit={onCommit} />)

    screen.getByRole('button', { name: 'Move crop' }).focus()
    await user.keyboard('{ArrowRight}')
    expect(onCommit).toHaveBeenLastCalledWith({ x: 0.35, y: 0, width: 0.32, height: 1 })

    await user.keyboard('{Shift>}{ArrowRight}{/Shift}')
    expect(onCommit).toHaveBeenLastCalledWith({ x: 0.44, y: 0, width: 0.32, height: 1 })
  })

  test('the crop never leaves the frame', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<CropOverlay crop={{ x: 0.68, y: 0, width: 0.32, height: 1 }} onCommit={onCommit} />)

    screen.getByRole('button', { name: 'Move crop' }).focus()
    await user.keyboard('{ArrowRight}')

    expect(onCommit).not.toHaveBeenCalled()
  })

  test('resizing keeps the canvas shape and stays centred on the crop', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(
      <CropOverlay crop={{ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }} onCommit={onCommit} />,
    )

    screen.getByRole('button', { name: 'Resize crop' }).focus()
    await user.keyboard('{ArrowDown}')

    const next = onCommit.mock.calls.at(-1)![0]
    expect(next.width).toBeCloseTo(0.48)
    expect(next.height).toBeCloseTo(0.48)
    expect(next.x).toBeCloseTo(0.26)
    expect(next.y).toBeCloseTo(0.26)
  })
})

describe('LayoutPanel', () => {
  test('Custom starts a crop in the canvas shape and Centred clears it', () => {
    const onCrop = vi.fn()
    render(
      <LayoutPanel
        item={{ id: 'scene-1', crop: null } as never}
        sourceAspect={16 / 9}
        canvasAspect={9 / 16}
        onCrop={onCrop}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
    expect(onCrop).toHaveBeenLastCalledWith(expect.objectContaining({ y: 0, height: 1 }))
    fireEvent.click(screen.getByRole('button', { name: 'Centred' }))
    expect(onCrop).toHaveBeenLastCalledWith(null)
  })
})
