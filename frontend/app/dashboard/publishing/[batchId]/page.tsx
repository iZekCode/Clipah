import { PublicationBatchDetail } from '@/features/publishing/PublicationBatchDetail'

/** One batch: the independent state, history, and controls of each destination in it. */
export default async function PublicationBatchPage({
  params,
}: {
  params: Promise<{ batchId: string }>
}) {
  const { batchId } = await params
  return <PublicationBatchDetail batchId={batchId} />
}
