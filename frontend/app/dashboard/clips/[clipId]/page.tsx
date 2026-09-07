import { ClipDetail } from '@/features/clips/ClipDetail'

/** One Clip: its edit versions, B-roll decisions, and exports. */
export default async function ClipPage({
  params,
  searchParams,
}: {
  params: Promise<{ clipId: string }>
  searchParams: Promise<{ projectId?: string; editId?: string; revision?: string }>
}) {
  const { clipId } = await params
  const { projectId, editId, revision } = await searchParams
  return (
    <ClipDetail
      candidateId={clipId}
      projectId={projectId ?? null}
      editId={editId ?? null}
      revision={revision === undefined ? null : Number(revision)}
    />
  )
}
