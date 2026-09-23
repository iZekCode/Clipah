import { ClipRedirect } from '@/features/clips/ClipRedirect'

/** One clip: resolved to its Project, then shown in review mode on that moment. */
export default async function ClipPage({
  params,
  searchParams,
}: {
  params: Promise<{ clipId: string }>
  searchParams: Promise<{ tab?: string }>
}) {
  const { clipId } = await params
  const { tab } = await searchParams
  return <ClipRedirect candidateId={clipId} tab={tab ?? null} />
}
