import { InviteAcceptance } from '@/features/team/InviteAcceptance'

/** Accept one one-time Workspace invitation after independent Session authentication. */
export default async function InvitePage({
  params,
}: {
  params: Promise<{ token: string }>
}) {
  const { token } = await params
  return <InviteAcceptance token={token} />
}
