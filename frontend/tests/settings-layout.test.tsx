import { screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'

import { SettingsLayout } from '@/features/settings/SettingsNav'

import { renderWithApi } from './support/api'

vi.mock('next/navigation', () => ({ usePathname: () => '/dashboard/team' }))

describe('Settings layout', () => {
  test('a side navigation reaches every settings section and marks the current page', () => {
    renderWithApi(
      <SettingsLayout description="Your workspace, its people, and where you are signed in.">
        <p>Members body</p>
      </SettingsLayout>,
    )

    const nav = screen.getByRole('navigation', { name: 'Settings sections' })
    expect(within(nav).getAllByRole('link').map((link) => [link.textContent, link.getAttribute('href')])).toEqual([
      ['General', '/dashboard/settings'],
      ['Members', '/dashboard/team'],
      ['Connections', '/dashboard/settings/connections'],
      ['Sessions', '/dashboard/settings#sessions'],
      ['Usage', '/dashboard/settings#usage'],
    ])
    expect(within(nav).getByRole('link', { name: 'Members' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('Members body')).toBeInTheDocument()
  })
})
