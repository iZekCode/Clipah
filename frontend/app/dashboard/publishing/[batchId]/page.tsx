import Link from 'next/link'

import { PublicationBatchDetail } from '@/features/publishing/PublicationBatchDetail'

/** One batch: the independent state, history, and controls of each destination in it. */
export default async function PublicationBatchPage({
  params,
}: {
  params: Promise<{ batchId: string }>
}) {
  const { batchId } = await params
  return (
    <section className="space-y-4">
      <nav aria-label="Breadcrumb" className="text-sm text-muted-foreground">
        <Link href="/dashboard/publishing" className="hover:text-foreground">
          Publishing
        </Link>{' '}
        / Batch
      </nav>
      <PublicationBatchDetail batchId={batchId} />
    </section>
  )
}
