import { NewPublication } from '@/features/publishing/NewPublication'

/** Start a publication, preselecting the export a Publish action came from. */
export default async function NewPublicationPage({
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
  const preselected =
    editId !== undefined && revision !== undefined && renderArtifactId !== undefined
      ? {
          editId,
          revision: Number(revision),
          renderArtifactId,
          durationMs: durationMs === undefined ? 0 : Number(durationMs),
        }
      : null
  return <NewPublication preselected={preselected} />
}
