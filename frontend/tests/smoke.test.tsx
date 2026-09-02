/**
 * Smoke tests for the product frontend.
 *
 * These cover the promises the shell has to keep before any feature is built on it:
 * the two pages render, a failed request surfaces its request identifier, the
 * subtitle and watermark controls serialize as real booleans rather than strings,
 * and text that happens to contain HTML tags is shown as text.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LandingPage from '@/app/page'
import { ClipOptionsForm } from '@/components/clip-options-form'
import { DashboardShell } from '@/components/dashboard-shell'
import { ErrorNotice } from '@/components/error-notice'
import { ApiError, apiFetch, CSRF_HEADER } from '@/lib/api/client'

const HTML_LOOKING_TEXT = '<img src=x onerror="alert(1)">'

describe('landing page', () => {
  it('names the product and offers a way to sign in', () => {
    render(<LandingPage />)

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/clipah/i)
    expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/signin')
  })
})

describe('dashboard shell', () => {
  it('renders the workspace navigation and the signed-in user for an authenticated view', () => {
    render(
      <DashboardShell
        user={{ displayName: 'Ada Lovelace' }}
        workspaceSwitcher={<p>Personal Workspace</p>}
        jobCenter={null}
      >
        <p>Projects live here.</p>
      </DashboardShell>,
    )

    expect(screen.getByRole('navigation', { name: /workspace/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Projects' })).toHaveAttribute(
      'href',
      '/dashboard/projects',
    )
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('Personal Workspace')).toBeInTheDocument()
    expect(screen.getByText('Projects live here.')).toBeInTheDocument()
  })

  it('renders provider and user text that contains HTML tags as literal text', () => {
    render(
      <DashboardShell
        user={{ displayName: HTML_LOOKING_TEXT }}
        workspaceSwitcher={<p>{HTML_LOOKING_TEXT}</p>}
        jobCenter={null}
      >
        <p>{HTML_LOOKING_TEXT}</p>
      </DashboardShell>,
    )

    expect(screen.getAllByText(HTML_LOOKING_TEXT).length).toBeGreaterThan(0)
    expect(document.querySelector('img')).toBeNull()
  })
})

describe('error rendering', () => {
  it('shows the public message and the request identifier of a failed request', () => {
    const error = new ApiError({
      status: 503,
      code: 'SERVICE_UNAVAILABLE',
      message: 'A required service is unavailable.',
      requestId: '018f3d1c-0f4c-7c3a-9a5e-2f2c9b0a7d21',
    })

    render(<ErrorNotice error={error} />)

    expect(screen.getByRole('alert')).toHaveTextContent('A required service is unavailable.')
    expect(screen.getByText(/018f3d1c-0f4c-7c3a-9a5e-2f2c9b0a7d21/)).toBeInTheDocument()
  })

  it('renders an error message containing HTML tags as literal text', () => {
    const error = new ApiError({
      status: 400,
      code: 'VALIDATION_ERROR',
      message: HTML_LOOKING_TEXT,
      requestId: 'request-1',
    })

    render(<ErrorNotice error={error} />)

    expect(screen.getByRole('alert')).toHaveTextContent(HTML_LOOKING_TEXT)
    expect(document.querySelector('img')).toBeNull()
  })
})

describe('clip option controls', () => {
  it('serializes subtitle and watermark choices as booleans, not strings', async () => {
    const user = userEvent.setup()
    const submitted = vi.fn()
    render(<ClipOptionsForm onSubmit={submitted} />)

    await user.click(screen.getByRole('switch', { name: /subtitles/i }))
    await user.click(screen.getByRole('button', { name: /save clip options/i }))

    expect(submitted).toHaveBeenCalledTimes(1)
    const payload = submitted.mock.calls[0]![0]
    expect(payload).toEqual({
      includeSubtitles: true,
      includeWatermark: false,
      watermarkText: null,
    })
    expect(typeof payload.includeSubtitles).toBe('boolean')
    expect(typeof payload.includeWatermark).toBe('boolean')
  })

  it('sends the watermark text only when the watermark is enabled', async () => {
    const user = userEvent.setup()
    const submitted = vi.fn()
    render(<ClipOptionsForm onSubmit={submitted} />)

    await user.click(screen.getByRole('switch', { name: /watermark/i }))
    await user.type(screen.getByRole('textbox', { name: /watermark text/i }), '@clipah')
    await user.click(screen.getByRole('button', { name: /save clip options/i }))

    expect(submitted).toHaveBeenCalledWith({
      includeSubtitles: false,
      includeWatermark: true,
      watermarkText: '@clipah',
    })
  })
})

describe('api client', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    document.cookie = 'clipah_csrf=double-submit-token'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.cookie = 'clipah_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
  })

  it('calls the same-origin API path with the session cookie attached', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ id: 'project-1' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )

    const body = await apiFetch<{ id: string }>('/api/v1/projects', { method: 'GET' })

    expect(body).toEqual({ id: 'project-1' })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v1/projects')
    expect(init.credentials).toBe('same-origin')
    expect(init.headers.get(CSRF_HEADER)).toBeNull()
  })

  it('echoes the double-submit token on a state-changing request', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))

    await apiFetch<void>('/api/v1/projects', { method: 'POST', body: '{}' })

    const [, init] = fetchMock.mock.calls[0]!
    expect(init.headers.get(CSRF_HEADER)).toBe('double-submit-token')
  })

  it('raises a typed error carrying the code and request identifier of a failure', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'NOT_FOUND',
            message: 'The requested resource was not found.',
            requestId: 'request-42',
          },
        }),
        { status: 404, headers: { 'content-type': 'application/json' } },
      ),
    )

    const failure = await apiFetch('/api/v1/projects/unknown', { method: 'GET' }).catch(
      (error: unknown) => error,
    )

    expect(failure).toBeInstanceOf(ApiError)
    const error = failure as ApiError
    expect(error.status).toBe(404)
    expect(error.code).toBe('NOT_FOUND')
    expect(error.requestId).toBe('request-42')
    expect(error.message).toBe('The requested resource was not found.')
  })

  it('raises a typed error when a failure body is not the error envelope', async () => {
    fetchMock.mockResolvedValue(new Response('<html>gateway timeout</html>', { status: 504 }))

    const failure = await apiFetch('/api/v1/projects', { method: 'GET' }).catch(
      (error: unknown) => error,
    )

    expect(failure).toBeInstanceOf(ApiError)
    const error = failure as ApiError
    expect(error.status).toBe(504)
    expect(error.code).toBe('UNKNOWN_ERROR')
    expect(error.requestId).toBeNull()
  })
})
