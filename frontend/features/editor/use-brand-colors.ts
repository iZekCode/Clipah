'use client'

import { useQuery } from '@tanstack/react-query'

import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { readVersionApiV1BrandKitsBrandKitIdVersionsVersionGet } from '@/lib/api/generated/brand-kits/brand-kits'
import type { BrandKitVersionResponse, CompositionV1 } from '@/lib/api/generated/model'

const NO_COLOURS: Array<{ name: string; hex: string }> = []

/**
 * The colours of the brand kit version this clip was built with, or none.
 *
 * A version never changes once published, so it is read once per visit.
 */
export function useBrandColors(
  brandKit: CompositionV1['brandKit'],
): Array<{ name: string; hex: string }> {
  const { active } = useWorkspaceScope()
  const version = useQuery<BrandKitVersionResponse, ApiError>({
    queryKey: ['/api/v1/brand-kits/version', active.id, brandKit?.id, brandKit?.version],
    queryFn: ({ signal }) =>
      readVersionApiV1BrandKitsBrandKitIdVersionsVersionGet(
        brandKit?.id ?? '',
        brandKit?.version ?? 0,
        { workspace_id: active.id },
        { signal },
      ),
    enabled: brandKit !== null,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  })
  return version.data?.definition.colors ?? NO_COLOURS
}
