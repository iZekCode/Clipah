import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { DashboardShell } from '@/components/dashboard-shell'
import { RAIL_PINNED_KEY } from '@/components/shell/navigation'

import { renderWithApi } from './support/api'
import { currentUser } from './support/fixtures'

const pathname = vi.hoisted(() => ({ current: '/dashboard/clips' }))
vi.mock('next/navigation', () => ({ usePathname: () => pathname.current }))

beforeEach(() => {
  pathname.current = '/dashboard/clips'
  window.localStorage.clear()
})

function shell(props: Partial<Parameters<typeof DashboardShell>[0]> = {}) {
  return renderWithApi(
    <DashboardShell user={currentUser()} workspaceSwitcher={<p>Workspace</p>} jobCenter={<p>Jobs</p>} {...props}>
      <p>Body</p>
    </DashboardShell>,
  )
}

describe('the Signal shell', () => {
  test('the rail names every destination and marks the current one', () => {
    shell()

    const rail = screen.getByRole('navigation', { name: 'Workspace' })
    for (const name of ['Home', 'Projects', 'Clips', 'Publishing', 'Settings']) {
      expect(within(rail).getByRole('link', { name })).toBeInTheDocument()
    }
    expect(within(rail).getByRole('link', { name: 'Clips' })).toHaveAttribute('aria-current', 'page')
  })

  test('the library opens from the rail', async () => {
    const user = userEvent.setup()
    shell()
    const rail = screen.getByRole('navigation', { name: 'Workspace' })

    await user.click(within(rail).getByRole('button', { name: 'Library' }))

    expect(within(rail).getByRole('link', { name: 'Assets' })).toHaveAttribute('href', '/dashboard/assets')
    expect(within(rail).getByRole('link', { name: 'Templates' })).toBeVisible()
    expect(within(rail).getByRole('link', { name: 'Brand kits' })).toBeVisible()
  })

  test('pinning the rail is remembered for this browser', async () => {
    const user = userEvent.setup()
    shell()

    await user.click(screen.getByRole('button', { name: 'Pin navigation' }))

    expect(screen.getByRole('button', { name: 'Collapse navigation' })).toHaveAttribute('aria-pressed', 'true')
    expect(window.localStorage.getItem(RAIL_PINNED_KEY)).toBe('true')
  })

  test('a broken storage does not break the rail', async () => {
    const user = userEvent.setup()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    shell()

    await user.click(screen.getByRole('button', { name: 'Pin navigation' }))

    expect(screen.getByRole('button', { name: 'Collapse navigation' })).toBeInTheDocument()
  })

  test('the top bar opens the command palette from its search trigger', async () => {
    const user = userEvent.setup()
    const onOpenCommandPalette = vi.fn()
    shell({ onOpenCommandPalette })

    await user.click(screen.getByRole('button', { name: /search or jump to/i }))

    expect(onOpenCommandPalette).toHaveBeenCalledTimes(1)
  })

  test('without a palette the top bar keeps the plain search form', () => {
    shell()

    expect(screen.getByRole('search')).toHaveAttribute('action', '/dashboard/search')
  })

  test('phones get a labelled tab bar and the full navigation behind one control', async () => {
    const user = userEvent.setup()
    shell()

    const tabs = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(tabs).getByRole('link', { name: 'Projects' })).toHaveAttribute('href', '/dashboard/projects')

    const toggle = within(tabs).getByRole('button', { name: 'Navigation' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })
})
