import { ReviewMode } from '@/features/review/ReviewMode'

/** Review one Project's moments, one at a time, from the keyboard. */
export default async function ReviewPage({
  params,
}: {
  params: Promise<{ projectId: string }>
}) {
  const { projectId } = await params
  return <ReviewMode projectId={projectId} />
}
