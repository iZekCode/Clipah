import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'vitest-axe'
import { describe, expect, test, vi } from 'vitest'

import { AccessibilityPanel } from '@/features/editor/AccessibilityPanel'
import type { AccessibilityWarningResponse } from '@/lib/api/generated/model'

const WARNING: AccessibilityWarningResponse = {
  code: 'caption_reading_speed',
  severity: 'warning',
  action: 'Shorten the caption text or leave it visible longer.',
  itemId: 'caption-1',
  startMs: 1_200,
  endMs: 2_000,
  measured: 21,
  threshold: 20,
}

describe('accessibility quality panel', () => {
  test('is axe-clean and moves the editor to the selected warning', async () => {
    const onSelect = vi.fn()
    const { container } = render(<AccessibilityPanel warnings={[WARNING]} onSelect={onSelect} />)

    expect(
      (await axe(container, { rules: { 'color-contrast': { enabled: false } } })).violations,
    ).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: WARNING.action }))
    expect(onSelect).toHaveBeenCalledWith({ itemId: 'caption-1', timeMs: 1_200 })
  })

  test('announces when the immutable revision has no warnings', () => {
    render(<AccessibilityPanel warnings={[]} onSelect={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent(/no accessibility warnings/i)
  })
})
