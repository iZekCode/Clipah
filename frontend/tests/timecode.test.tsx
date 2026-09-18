import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { NumberScrub } from '@/components/ui/number-scrub'
import { TimecodeInput } from '@/components/ui/timecode-input'
import { formatRuler, formatTimecode, parseTimecode } from '@/lib/time/timecode'

/** jsdom has no `PointerEvent`; a mouse event of the pointer type carries the `clientX` read. */
function pointer(element: Element, type: string, clientX: number): void {
  fireEvent(element, new MouseEvent(type, { bubbles: true, clientX }))
}

describe('timecodes', () => {
  test('format minutes, seconds, and hundredths', () => {
    expect(formatTimecode(0)).toBe('0:00.00')
    expect(formatTimecode(1_500)).toBe('0:01.50')
    expect(formatTimecode(21_000)).toBe('0:21.00')
    expect(formatTimecode(725_555)).toBe('12:05.56')
    expect(formatRuler(65_900)).toBe('1:05')
  })

  test('read back every form a member types, and refuse the rest', () => {
    expect(parseTimecode('0:21.00')).toBe(21_000)
    expect(parseTimecode('12:05.56')).toBe(725_560)
    expect(parseTimecode('1:05')).toBe(65_000)
    expect(parseTimecode('21')).toBe(21_000)
    expect(parseTimecode('1.5')).toBe(1_500)
    expect(parseTimecode(' 0:01.05 ')).toBe(1_050)
    expect(parseTimecode('1:75')).toBeNull()
    expect(parseTimecode('abc')).toBeNull()
    expect(parseTimecode('')).toBeNull()
  })
})

describe('TimecodeInput', () => {
  test('shows a timecode and commits milliseconds when the member leaves the field', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<TimecodeInput label="End" valueMs={31_000} onCommit={onCommit} />)

    const field = screen.getByRole('textbox', { name: 'End' })
    expect(field).toHaveValue('0:31.00')
    await user.clear(field)
    await user.type(field, '0:21.00')
    expect(onCommit).not.toHaveBeenCalled()
    await user.tab()

    expect(onCommit).toHaveBeenCalledWith(21_000)
  })

  test('arrow keys nudge by a hundredth, and by a second with Shift', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<TimecodeInput label="Start" valueMs={1_000} onCommit={onCommit} />)

    screen.getByRole('textbox', { name: 'Start' }).focus()
    await user.keyboard('{ArrowUp}')
    await user.keyboard('{Shift>}{ArrowDown}{/Shift}')

    expect(onCommit).toHaveBeenNthCalledWith(1, 1_010)
    expect(onCommit).toHaveBeenNthCalledWith(2, 0)
  })

  test('an accessible name can stand in for a hidden label', () => {
    render(
      <TimecodeInput
        label="From"
        hideLabel
        accessibleName="Start of w000002"
        valueMs={1_000}
        onCommit={vi.fn()}
      />,
    )

    expect(screen.getByRole('textbox', { name: 'Start of w000002' })).toHaveValue('0:01.00')
  })

  test('explains an unreadable or out-of-range time and keeps the value', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<TimecodeInput label="End" valueMs={5_000} maxMs={10_000} onCommit={onCommit} />)
    const field = screen.getByRole('textbox', { name: 'End' })

    await user.clear(field)
    await user.type(field, 'soon{Enter}')
    expect(screen.getByRole('alert')).toHaveTextContent('Use m:ss.cc, for example 0:21.50')
    expect(field).toHaveAttribute('aria-invalid', 'true')

    await user.clear(field)
    await user.type(field, '0:12.00{Enter}')
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Choose a time between 0:00.00 and 0:10.00',
    )
    expect(onCommit).not.toHaveBeenCalled()
  })
})

describe('NumberScrub', () => {
  test('commits a typed value clamped to its range, named by its accessible name', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(
      <NumberScrub
        label="Letter spacing"
        accessibleName="Caption letter spacing"
        value={0}
        min={-10}
        max={40}
        step={0.5}
        precision={1}
        onCommit={onCommit}
      />,
    )

    const field = screen.getByRole('textbox', { name: 'Caption letter spacing' })
    await user.clear(field)
    await user.type(field, '99')
    await user.tab()

    expect(onCommit).toHaveBeenCalledWith(40)
  })

  test('dragging the label previews and commits once on release', () => {
    const onCommit = vi.fn()
    render(<NumberScrub label="Size" value={64} min={12} max={200} step={1} onCommit={onCommit} />)
    const label = screen.getByText('Size')

    pointer(label, 'pointerdown', 0)
    pointer(label, 'pointermove', 40)
    expect(screen.getByRole('textbox', { name: 'Size' })).toHaveValue('74')
    expect(onCommit).not.toHaveBeenCalled()
    pointer(label, 'pointerup', 40)

    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith(74)
  })
})
