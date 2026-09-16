import { Plus } from 'lucide-react'
import Link from 'next/link'

import { PageHeader } from '@/components/page-header'
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
        description="Every destination keeps its own state, so a batch never hides a failure behind a success."
        actions={
          <Link
            href="/dashboard/publishing/new"
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            <Plus aria-hidden="true" className="size-4" />
            New publication
          </Link>
        }
      />
      <PublicationHistory />
    </section>
  )
}
