import { PublicationComposer } from '@/features/publishing/PublicationComposer'
import { PublicationHistory } from '@/features/publishing/PublicationHistory'

/**
 * Publishing: what has been published, and the composer for one exact rendered artifact.
 *
 * The composer only appears where the caller named the Edit revision and the render it
 * approved, because a destination without an artifact behind it is not a publication.
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
  const composing =
    editId !== undefined && revision !== undefined && renderArtifactId !== undefined

  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Publishing</h1>
        <p className="text-sm text-muted-foreground">
          Every destination keeps its own state, so a batch never hides a failure behind a
          success.
        </p>
      </div>
      {composing ? (
        <PublicationComposer
          editId={editId}
          revision={Number(revision)}
          renderArtifactId={renderArtifactId}
          renderDigest={null}
          durationMs={durationMs === undefined ? 0 : Number(durationMs)}
        />
      ) : null}
      <PublicationHistory />
    </section>
  )
}
