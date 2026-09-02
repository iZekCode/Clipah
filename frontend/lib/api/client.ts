/**
 * The one way the browser talks to the backend.
 *
 * Requests are same-origin, so the Session cookie stays first-party and the backend's
 * CSRF origin check sees its own site. State-changing requests echo the double-submit
 * token the backend set in a readable cookie. Failures arrive as the backend's sanitized
 * error envelope and leave here as an `ApiError` that carries the request identifier a
 * user can quote in a support request.
 */

export const CSRF_HEADER = 'X-CSRF-Token'
export const CSRF_COOKIE_NAMES = ['__Host-clipah_csrf', 'clipah_csrf'] as const
const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const UNKNOWN_ERROR_CODE = 'UNKNOWN_ERROR'
const UNKNOWN_ERROR_MESSAGE = 'Something went wrong. Please try again.'

/** The failure shape every backend route promises, whatever went wrong behind it. */
export interface ApiErrorEnvelope {
  code: string
  message: string
  requestId: string | null
}

/** A failed API call, carrying only fields that are safe to show a user. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null

  constructor({
    status,
    code,
    message,
    requestId,
  }: ApiErrorEnvelope & { status: number }) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

/** Read the double-submit token the backend made readable for this client. */
export function readCsrfToken(): string | null {
  if (typeof document === 'undefined') {
    return null
  }
  for (const cookie of document.cookie.split(';')) {
    const separator = cookie.indexOf('=')
    if (separator === -1) {
      continue
    }
    const name = cookie.slice(0, separator).trim()
    if ((CSRF_COOKIE_NAMES as readonly string[]).includes(name)) {
      return decodeURIComponent(cookie.slice(separator + 1).trim())
    }
  }
  return null
}

/**
 * Perform one API call and return its parsed body, or throw an `ApiError`.
 *
 * This is also the mutator the generated client is built on, so every generated
 * hook inherits the same cookie, CSRF, and error behaviour.
 */
export async function apiFetch<T>(url: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (init.body !== undefined && init.body !== null && !headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }
  if (UNSAFE_METHODS.has(method)) {
    const token = readCsrfToken()
    if (token !== null) {
      headers.set(CSRF_HEADER, token)
    }
  }

  const response = await fetch(url, {
    ...init,
    method,
    headers,
    credentials: 'same-origin',
  })

  if (!response.ok) {
    throw await toApiError(response)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

/** Turn a failed response into the typed error the UI renders. */
async function toApiError(response: Response): Promise<ApiError> {
  const envelope = await readEnvelope(response)
  return new ApiError({ status: response.status, ...envelope })
}

/** Read the error envelope, tolerating a body written by something other than the API. */
async function readEnvelope(response: Response): Promise<ApiErrorEnvelope> {
  const fallback: ApiErrorEnvelope = {
    code: UNKNOWN_ERROR_CODE,
    message: UNKNOWN_ERROR_MESSAGE,
    requestId: null,
  }
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    return fallback
  }
  if (typeof payload !== 'object' || payload === null || !('error' in payload)) {
    return fallback
  }
  const error = (payload as { error: unknown }).error
  if (typeof error !== 'object' || error === null) {
    return fallback
  }
  const { code, message, requestId } = error as Record<string, unknown>
  return {
    code: typeof code === 'string' ? code : fallback.code,
    message: typeof message === 'string' ? message : fallback.message,
    requestId: typeof requestId === 'string' ? requestId : null,
  }
}
