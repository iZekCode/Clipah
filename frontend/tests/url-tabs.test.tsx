import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test } from 'vitest'

import { TabList, TabPanel, useUrlTab } from '@/components/url-tabs'

const TABS = [
  { id: 'exports', label: 'Exports' },
  { id: 'broll', label: 'B-roll' },
] as const

function Tabs() {
  const [tab, choose] = useUrlTab(['exports', 'broll'] as const, 'exports')
  return (
    <>
      <TabList label="Clip sections" tabs={TABS} active={tab} onChoose={choose} idPrefix="clip" />
      <TabPanel idPrefix="clip" id="exports" active={tab}>
        Exports body
      </TabPanel>
      <TabPanel idPrefix="clip" id="broll" active={tab}>
        B-roll body
      </TabPanel>
    </>
  )
}

beforeEach(() => {
  window.history.replaceState(null, '', '/dashboard/clips/one')
})

describe('URL tabs', () => {
  test('opens the tab a link named and keeps the choice in the address', async () => {
    window.history.replaceState(null, '', '/dashboard/clips/one?tab=broll')
    const user = userEvent.setup()
    render(<Tabs />)

    expect(await screen.findByText('B-roll body')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Exports' }))

    expect(screen.getByRole('tab', { name: 'Exports' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Exports body')).toBeInTheDocument()
    expect(window.location.search).toBe('?tab=exports')
  })

  test('ignores an unknown tab', () => {
    window.history.replaceState(null, '', '/dashboard/clips/one?tab=nope')
    render(<Tabs />)

    expect(screen.getByText('Exports body')).toBeInTheDocument()
  })
})
