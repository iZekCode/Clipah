import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Film } from 'lucide-react'
import { describe, expect, test, vi } from 'vitest'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { ApiError } from '@/lib/api/client'

vi.mock('@/lib/notify', () => ({ notify: { success: vi.fn(), info: vi.fn(), failure: vi.fn() } }))

import { notify } from '@/lib/notify'

describe('shared page components', () => {
  test('a page title is the one level-one heading, set in display type', () => {
    render(<PageHeader title="Projects" description="Every video you brought in." />)

    const heading = screen.getByRole('heading', { level: 1, name: 'Projects' })
    expect(heading).toHaveClass('font-display', 'text-h1')
  })

  test('a status is words with a decorative dot, never colour alone', () => {
    render(<StatusBadge tone="success">Ready to review</StatusBadge>)

    const status = screen.getByText('Ready to review')
    expect(status).toBeInTheDocument()
    expect(status.querySelector('[aria-hidden="true"]')).not.toBeNull()
  })

  test('an empty state is a left-aligned invitation with no tinted icon square', () => {
    const { container } = render(
      <EmptyState icon={Film} title="Drop a long video to start" description="Clipah finds the moments." />,
    )

    expect(screen.getByText('Drop a long video to start')).toBeInTheDocument()
    expect(container.firstElementChild).not.toHaveClass('text-center')
    expect(container.querySelector('.rounded-full')).toBeNull()
  })

  test('an error keeps the reference out of the sentence and copies it on request', async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    const error = new ApiError({
      status: 503,
      code: 'SERVICE_UNAVAILABLE',
      message: 'A required service is unavailable.',
      requestId: 'request-1234',
    })
    const onRetry = vi.fn()

    render(<ErrorNotice error={error} onRetry={onRetry} />)

    expect(screen.getByRole('alert')).toHaveTextContent('A required service is unavailable.')
    expect(screen.getByText('Ref request-1234')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Copy details' }))
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(writeText).toHaveBeenCalledWith('SERVICE_UNAVAILABLE request-1234')
    expect(notify.success).toHaveBeenCalledWith('Details copied')
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
