/** One Clip: its edit versions, B-roll decisions, and exports. */
export default async function ClipPage({ params }: { params: Promise<{ clipId: string }> }) {
  const { clipId } = await params
  return (
    <section className="space-y-2">
      <h1 className="text-2xl font-semibold tracking-tight">Clip</h1>
      <p className="text-sm text-muted-foreground">
        Edit versions, B-roll decisions, and exports for clip {clipId} appear here.
      </p>
    </section>
  )
}
