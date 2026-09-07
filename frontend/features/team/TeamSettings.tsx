'use client'

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useSession } from '@/features/auth/session'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  createWorkspaceInviteApiV1WorkspacesWorkspaceIdInvitesPost,
  listWorkspaceInvitesApiV1WorkspacesWorkspaceIdInvitesGet,
  removeWorkspaceMemberApiV1WorkspacesWorkspaceIdMembersMemberUserIdDelete,
  revokeWorkspaceInviteApiV1WorkspacesWorkspaceIdInvitesInviteIdDelete,
  transferWorkspaceOwnershipApiV1WorkspacesWorkspaceIdOwnershipTransfersPost,
  updateWorkspaceMemberApiV1WorkspacesWorkspaceIdMembersMemberUserIdPatch,
} from '@/lib/api/generated/workspace-memberships/workspace-memberships'
import { membersApiV1WorkspacesWorkspaceIdMembersGet } from '@/lib/api/generated/workspaces/workspaces'
import type {
  InviteResponse,
  MemberCollectionResponse,
  PendingInviteCollectionResponse,
  WorkspaceRole,
} from '@/lib/api/generated/model'

const ASSIGNABLE_ROLES: WorkspaceRole[] = ['admin', 'editor', 'reviewer', 'viewer']

/** Role-aware team membership and one-time invitation controls for the active Workspace. */
export function TeamSettings() {
  const { active } = useWorkspaceScope()
  const session = useSession()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<WorkspaceRole>('reviewer')
  const [invite, setInvite] = useState<InviteResponse | null>(null)
  const [mutationError, setMutationError] = useState<ApiError | null>(null)
  const enabled = session.data?.capabilities.collaboration === true
  const canManage = active.role === 'owner' || active.role === 'admin'
  const members = useQuery<MemberCollectionResponse, ApiError>({
    queryKey: ['/api/v1/workspace-members', active.id],
    queryFn: ({ signal }) => membersApiV1WorkspacesWorkspaceIdMembersGet(active.id, { signal }),
    retry: false,
    enabled,
  })
  const pending = useQuery<PendingInviteCollectionResponse, ApiError>({
    queryKey: ['/api/v1/workspace-invites', active.id],
    queryFn: ({ signal }) => listWorkspaceInvitesApiV1WorkspacesWorkspaceIdInvitesGet(active.id, { signal }),
    retry: false,
    enabled,
  })

  if (session.isPending) return <p role="status">Loading team settings…</p>
  if (session.isError) return <ErrorNotice error={session.error} />
  if (!enabled) return <p>Workspace collaboration is not enabled for this plan.</p>

  async function createInvite() {
    setMutationError(null)
    try {
      setInvite(await createWorkspaceInviteApiV1WorkspacesWorkspaceIdInvitesPost(active.id, { email, role }))
      setEmail('')
      await pending.refetch()
    } catch (error) {
      setMutationError(error as ApiError)
    }
  }

  async function changeRole(userId: string, nextRole: WorkspaceRole) {
    setMutationError(null)
    try {
      await updateWorkspaceMemberApiV1WorkspacesWorkspaceIdMembersMemberUserIdPatch(active.id, userId, { role: nextRole })
      await members.refetch()
    } catch (error) {
      setMutationError(error as ApiError)
    }
  }

  async function remove(userId: string) {
    setMutationError(null)
    try {
      await removeWorkspaceMemberApiV1WorkspacesWorkspaceIdMembersMemberUserIdDelete(active.id, userId)
      await members.refetch()
    } catch (error) {
      setMutationError(error as ApiError)
    }
  }

  async function transfer(userId: string) {
    setMutationError(null)
    try {
      await transferWorkspaceOwnershipApiV1WorkspacesWorkspaceIdOwnershipTransfersPost(active.id, { userId })
      await members.refetch()
    } catch (error) {
      setMutationError(error as ApiError)
    }
  }

  async function revoke(inviteId: string) {
    setMutationError(null)
    try {
      await revokeWorkspaceInviteApiV1WorkspacesWorkspaceIdInvitesInviteIdDelete(active.id, inviteId)
      await pending.refetch()
    } catch (error) {
      setMutationError(error as ApiError)
    }
  }

  return (
    <section aria-labelledby="team-settings-heading" className="space-y-4">
      <h2 id="team-settings-heading" className="text-lg font-semibold">Members</h2>
      {members.isPending ? <p role="status">Loading members…</p> : null}
      {members.isError ? <ErrorNotice error={members.error} /> : null}
      {pending.isError ? <ErrorNotice error={pending.error} /> : null}
      {mutationError === null ? null : <ErrorNotice error={mutationError} />}
      <ul className="divide-y rounded border">
        {(members.data?.members ?? []).map((member) => (
          <li key={member.userId} className="flex flex-wrap items-center justify-between gap-3 p-3">
            <div>
              <p className="font-medium">{member.displayName}</p>
              <p className="text-sm text-muted-foreground">{member.email} · {member.role}</p>
            </div>
            {canManage && member.role !== 'owner' ? (
              <div className="flex flex-wrap items-center gap-2">
                <label className="sr-only" htmlFor={`role-${member.userId}`}>Role for {member.displayName}</label>
                <select id={`role-${member.userId}`} value={member.role} onChange={(event) => void changeRole(member.userId, event.target.value as WorkspaceRole)} className="rounded border bg-background p-2 text-sm">
                  {ASSIGNABLE_ROLES.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
                {active.role === 'owner' ? <button type="button" onClick={() => void transfer(member.userId)} className="rounded border px-2 py-1 text-xs">Transfer ownership</button> : null}
                <button type="button" onClick={() => void remove(member.userId)} className="rounded border px-2 py-1 text-xs">Remove {member.displayName}</button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>

      <section aria-labelledby="pending-invites-heading" className="space-y-2">
        <h3 id="pending-invites-heading" className="font-medium">Pending invites</h3>
        {pending.isPending ? <p role="status">Loading invites…</p> : null}
        <ul className="space-y-2">
          {(pending.data?.invites ?? []).map((item) => (
            <li key={item.id} className="flex items-center justify-between gap-2 rounded border p-2 text-sm">
              <span>{item.email} · {item.role}</span>
              {canManage ? <button type="button" onClick={() => void revoke(item.id)} className="text-xs underline">Revoke invite for {item.email}</button> : null}
            </li>
          ))}
        </ul>
      </section>

      {canManage ? (
        <form className="space-y-2 rounded border p-3" onSubmit={(event) => { event.preventDefault(); void createInvite() }}>
          <h3 className="font-medium">Invite a member</h3>
          <label className="block text-sm" htmlFor="invite-email">Invite email</label>
          <input id="invite-email" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} className="w-full rounded border bg-background p-2" />
          <label className="block text-sm" htmlFor="invite-role">Invite role</label>
          <select id="invite-role" value={role} onChange={(event) => setRole(event.target.value as WorkspaceRole)} className="w-full rounded border bg-background p-2">
            {ASSIGNABLE_ROLES.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          <button type="submit" className="rounded bg-primary px-3 py-2 text-sm text-primary-foreground">Create invite</button>
          {invite === null ? null : (
            <div role="status" className="space-y-1">
              <label className="block text-sm" htmlFor="invite-link">Invite link (shown once)</label>
              <input id="invite-link" readOnly value={`${window.location.origin}/invite/${invite.token}`} className="w-full rounded border bg-muted p-2 text-sm" />
            </div>
          )}
        </form>
      ) : null}
    </section>
  )
}
