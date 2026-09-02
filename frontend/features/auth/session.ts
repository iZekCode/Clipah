'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { readCurrentUserApiV1MeGet } from '@/lib/api/generated/auth/auth'
import type { CurrentUserResponse } from '@/lib/api/generated/model'
import { ApiError } from '@/lib/api/client'

export const SESSION_QUERY_KEY = ['/api/v1/me'] as const

/**
 * Read the signed-in User from the backend.
 *
 * The Session lives in a cookie the browser cannot read, so the only honest answer to
 * "who is signed in" is the one the backend gives. A refusal is a decision, not a
 * transport failure, so it is never retried.
 */
export function useSession(): UseQueryResult<CurrentUserResponse, ApiError> {
  return useQuery<CurrentUserResponse, ApiError>({
    queryKey: SESSION_QUERY_KEY,
    queryFn: ({ signal }) => readCurrentUserApiV1MeGet({ signal }),
    retry: false,
    staleTime: 30_000,
  })
}

/** Whether a failure means nobody is signed in rather than something went wrong. */
export function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}
