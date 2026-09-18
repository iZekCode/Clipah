import { Plus } from 'lucide-react'
import Link from 'next/link'

import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { NewPublication } from '@/features/publishing/NewPublication'
import { PublicationHistory } from '@/features/publishing/PublicationHistory'

/**
 * Publishing: the queue and history of every destination, and the way to start another.
 *
 * Older links that named an export directly still open the composer for it, so a
 * bookmarked Publish action keeps working.
 */
export default async function PublishingPage({
  searchParams,
}: {
  searchParams: Promise<{
    editId?: string
    revision?: string
    renderArtifactId?: string
    durationMs?: string
  }>
}) {
  const { editId, revision, renderArtifactId, durationMs } = await searchParams
  if (editId !== undefined && revision !== undefined && renderArtifactId !== undefined) {
    return (
      <NewPublication
        preselected={{
          editId,
          revision: Number(revision),
          renderArtifactId,
          durationMs: durationMs === undefined ? 0 : Number(durationMs),
        }}
      />
    )
  }

  return (
    <section className="space-y-2">
      <PageHeader
        title="Publishing"
        description="Schedule clips to your connected accounts."
        actions={
          <Button asChild>
            <Link href="/dashboard/publishing/new">
              <Plus aria-hidden="true" strokeWidth={1.75} />
              New publication
            </Link>
          </Button>
        }
      />
      <PublicationHistory />
    </section>
  )
}
