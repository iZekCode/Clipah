import { ClipDetail } from '@/features/clips/ClipDetail'

/** One Clip: its edit versions, B-roll decisions, and exports. */
export default async function ClipPage({
  params,
  searchParams,
}: {
  params: Promise<{ clipId: string }>
  searchParams: Promise<{ projectId?: string }>
}) {
  const { clipId } = await params
  const { projectId } = await searchParams
  return <ClipDetail candidateId={clipId} projectId={projectId ?? null} />
}
