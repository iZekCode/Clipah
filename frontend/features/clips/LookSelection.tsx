'use client'

import { useQuery } from '@tanstack/react-query'

import { Select } from '@/components/ui/select'
import type { ApiError } from '@/lib/api/client'
import { listCollectionApiV1BrandKitsGet } from '@/lib/api/generated/brand-kits/brand-kits'
import type { BrandKitListResponse, TemplateListResponse } from '@/lib/api/generated/model'
import { listCollectionApiV1TemplatesGet } from '@/lib/api/generated/templates/templates'

/**
 * The look and the brand this clip will be opened with.
 *
 * Both lists are read best-effort: a Workspace that has published neither, or a read that
 * fails, must not stop somebody opening their own clip. Choosing nothing is the ordinary
 * case, and it is what the control starts on.
 */
export function LookSelection({
  workspaceId,
  templateId,
  brandKitId,
  onTemplate,
  onBrandKit,
}: {
  workspaceId: string
  templateId: string
  brandKitId: string
  onTemplate: (value: string) => void
  onBrandKit: (value: string) => void
}) {
  const templates = useQuery<TemplateListResponse, ApiError>({
    queryKey: ['/api/v1/templates', workspaceId],
    queryFn: ({ signal }) =>
      listCollectionApiV1TemplatesGet({ workspace_id: workspaceId }, { signal }),
    retry: false,
  })
  const kits = useQuery<BrandKitListResponse, ApiError>({
    queryKey: ['/api/v1/brand-kits', workspaceId],
    queryFn: ({ signal }) =>
      listCollectionApiV1BrandKitsGet({ workspace_id: workspaceId }, { signal }),
    retry: false,
  })

  const looks = templates.data?.templates ?? []
  const brands = kits.data?.brandKits ?? []
  if (looks.length === 0 && brands.length === 0) {
    return null
  }

  return (
    <>
      {looks.length === 0 ? null : (
        <label className="flex items-center gap-1.5 text-caption">
          <span className="text-muted-foreground">Look</span>
          <Select
            controlSize="sm"
            value={templateId}
            onChange={(event) => onTemplate(event.target.value)}
          >
            <option value="">None</option>
            {looks.map((look) => (
              <option key={look.id} value={look.id}>
                {look.name} (v{look.version})
              </option>
            ))}
          </Select>
        </label>
      )}
      {brands.length === 0 ? null : (
        <label className="flex items-center gap-1.5 text-caption">
          <span className="text-muted-foreground">Brand</span>
          <Select
            controlSize="sm"
            value={brandKitId}
            onChange={(event) => onBrandKit(event.target.value)}
          >
            <option value="">None</option>
            {brands.map((kit) => (
              <option key={kit.id} value={kit.id}>
                {kit.name} (v{kit.version})
              </option>
            ))}
          </Select>
        </label>
      )}
    </>
  )
}
