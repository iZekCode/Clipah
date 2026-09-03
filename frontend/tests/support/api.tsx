import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { vi } from 'vitest'

/** One recorded request, as the assertions in a test want to read it. */
export interface RecordedRequest {
  method: string
  path: string
  params: URLSearchParams
  headers: Headers
  body: unknown
}

/** What a stubbed endpoint answers with. */
export interface StubbedResponse {
  status?: number
  body?: unknown
  headers?: Record<string, string>
}

export type Handler = StubbedResponse | ((request: RecordedRequest) => StubbedResponse)

/** The stubbed backend a test drives its components against. */
export interface StubbedApi {
  calls: RecordedRequest[]
  /** Replace one route's answer after the component under test has already rendered. */
  set: (route: string, handler: Handler) => void
}

const ERROR_MESSAGES: Record<number, string> = {
  401: 'You are not signed in.',
  403: 'You cannot do that.',
  404: 'That resource was not found.',
  409: 'That conflicts with the current state.',
  500: 'Something went wrong. Please try again.',
}

/**
 * Answer the one `fetch` the API client makes, and remember every request.
 *
 * Routes are keyed by method and path (`GET /api/v1/projects`) because the query string
 * carries the Workspace, which most assertions want to read rather than match on.
 */
export function stubApi(handlers: Record<string, Handler>): StubbedApi {
  const routes = new Map<string, Handler>(Object.entries(handlers))
  const calls: RecordedRequest[] = []

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input), 'https://clipah.test')
      const method = (init.method ?? 'GET').toUpperCase()
      const request: RecordedRequest = {
        method,
        path: url.pathname,
        params: url.searchParams,
        headers: new Headers(init.headers),
        body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
      }
      calls.push(request)

      const handler = routes.get(`${method} ${url.pathname}`)
      if (handler === undefined) {
        return jsonResponse(404, errorBody(404))
      }
      const answer = typeof handler === 'function' ? handler(request) : handler
      const status = answer.status ?? 200
      if (status >= 400) {
        return jsonResponse(status, answer.body ?? errorBody(status), answer.headers)
      }
      return jsonResponse(status, answer.body, answer.headers)
    }),
  )

  return {
    calls,
    set: (route, handler) => {
      routes.set(route, handler)
    },
  }
}

/** Render one component with the same query behaviour the application configures. */
export function renderWithApi(ui: ReactElement): RenderResult {
  return render(ui, { wrapper: ApiWrapper })
}

/** The provider tree every component under test needs, with retries off. */
export function ApiWrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

/** Build the sanitized error envelope every failure leaves the backend as. */
export function errorBody(status: number, code = codeFor(status)): unknown {
  return {
    error: {
      code,
      message: ERROR_MESSAGES[status] ?? 'Something went wrong. Please try again.',
      requestId: 'request-1234',
    },
  }
}

function codeFor(status: number): string {
  if (status === 401) return 'UNAUTHENTICATED'
  if (status === 403) return 'FORBIDDEN'
  if (status === 404) return 'NOT_FOUND'
  if (status === 409) return 'CONFLICT'
  return 'INTERNAL_ERROR'
}

function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  if (body === undefined) {
    return new Response(null, { status: status >= 400 ? status : 204, headers })
  }
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  })
}
