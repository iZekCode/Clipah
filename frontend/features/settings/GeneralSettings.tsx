'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { Field, inputClassName } from '@/components/field'
import { LoadingState } from '@/components/loading-state'
import { Section } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { WORKSPACES_QUERY_KEY, useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import {
  listSessionsApiV1MeSessionsGet,
  revokeOneSessionApiV1MeSessionsSessionIdDelete,
  revokeOtherSessionsApiV1MeSessionsDelete,
} from '@/lib/api/generated/auth/auth'
import { showApiV1DashboardSummaryGet } from '@/lib/api/generated/dashboard/dashboard'
import type {
  DashboardSummaryResponse,
  PublishingRolePolicy,
  QuotaResource,
  WorkspaceResponse,
} from '@/lib/api/generated/model'
import { updateApiV1WorkspacesWorkspaceIdPatch } from '@/lib/api/generated/workspaces/workspaces'

import { SettingsLayout } from './SettingsNav'

const USAGE_LABELS: Record<QuotaResource, string> = {
  analyses: 'Analyses',
  stock_requests: 'Stock requests',
  generated_images: 'Generated images',
  generated_videos: 'Generated videos',
  generated_seconds: 'Generated seconds',
  social_publications: 'Social publications',
}

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  editor: 'Editor',
  reviewer: 'Reviewer',
  viewer: 'Viewer',
}

/** The General section of Settings: this Workspace, its monthly usage, and your sessions. */
export function GeneralSettings() {
  return (
    <SettingsLayout description="Manage this workspace, see what it has used this month, and control where you are signed in.">
      <WorkspaceDetails />
      <UsageDetails />
      <SessionDetails />
    </SettingsLayout>
  )
}

/** The Workspace's name and publishing policy, changeable only by its stewards. */
function WorkspaceDetails() {
  const { active } = useWorkspaceScope()
  const steward = active.role === 'owner' || active.role === 'admin'
  return (
    <Section title="Workspace" description="Owners and admins can rename the workspace and decide who may publish.">
      <div className="space-y-4 rounded-lg border p-5">
        <dl className="grid gap-3 text-small sm:grid-cols-3">
          <div>
            <dt className="text-caption text-muted-foreground">Type</dt>
            <dd className="font-medium">{active.kind === 'personal' ? 'Personal' : 'Team'}</dd>
          </div>
          <div>
            <dt className="text-caption text-muted-foreground">Your role</dt>
            <dd className="font-medium">{ROLE_LABELS[active.role] ?? active.role}</dd>
          </div>
          <div>
            <dt className="text-caption text-muted-foreground">Created</dt>
            <dd className="font-mono">{new Date(active.createdAt).toLocaleDateString()}</dd>
          </div>
        </dl>
        {steward ? <WorkspaceForm key={active.id} workspace={active} /> : (
          <p className="text-small text-muted-foreground">
            {active.name} · publishing is allowed for{' '}
            {active.publishingRolePolicy === 'owner_admin' ? 'owners and admins' : 'owners, admins, and editors'}.
          </p>
        )}
      </div>
    </Section>
  )
}

function WorkspaceForm({ workspace }: { workspace: WorkspaceResponse }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(workspace.name)
  const [policy, setPolicy] = useState<PublishingRolePolicy>(workspace.publishingRolePolicy)
  const save = useMutation<WorkspaceResponse, ApiError>({
    mutationFn: () =>
      updateApiV1WorkspacesWorkspaceIdPatch(workspace.id, {
        ...(name.trim() === workspace.name ? {} : { name: name.trim() }),
        ...(policy === workspace.publishingRolePolicy ? {} : { publishingRolePolicy: policy }),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: WORKSPACES_QUERY_KEY }),
  })
  const unchanged = name.trim() === workspace.name && policy === workspace.publishingRolePolicy

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!unchanged && name.trim() !== '') {
      save.mutate()
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
      <Field label="Workspace name">
        <input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} className={inputClassName} />
      </Field>
      <Field label="Who may publish">
        <Select
          value={policy}
          onChange={(event) => setPolicy(event.target.value as PublishingRolePolicy)}
          wrapperClassName="w-full"
        >
          <option value="owner_admin_editor">Owners, admins, and editors</option>
          <option value="owner_admin">Owners and admins only</option>
        </Select>
      </Field>
      <div className="flex items-center gap-3 sm:col-span-2">
        <Button type="submit" disabled={unchanged || save.isPending}>
          {save.isPending ? 'Saving…' : 'Save changes'}
        </Button>
        {save.isSuccess && unchanged ? (
          <p role="status" className="text-small text-success">Saved.</p>
        ) : null}
      </div>
      {save.isError ? (
        <div className="sm:col-span-2">
          <ErrorNotice error={save.error} />
        </div>
      ) : null}
    </form>
  )
}

/** What this Workspace has spent against each monthly allowance, as the backend counts it. */
function UsageDetails() {
  const { active } = useWorkspaceScope()
  const summary = useQuery<DashboardSummaryResponse, ApiError>({
    queryKey: ['/api/v1/dashboard/summary', active.id],
    queryFn: ({ signal }) => showApiV1DashboardSummaryGet({ workspace_id: active.id }, { signal }),
    retry: false,
  })

  return (
    <Section id="usage" title="Usage this month" description="Metered work counts against these monthly allowances.">
      {summary.isPending ? (
        <LoadingState label="Loading usage…" />
      ) : summary.isError ? (
        <ErrorNotice error={summary.error} onRetry={() => void summary.refetch()} />
      ) : (
        <ul aria-label="Usage" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {summary.data.usage.map((entry) => (
            <li key={entry.resource}>
              <div role="group" aria-label={USAGE_LABELS[entry.resource]} className="space-y-2 rounded-lg border bg-card p-4">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-small font-medium">{USAGE_LABELS[entry.resource]}</span>
                  <span className="tabular font-mono text-caption text-muted-foreground">
                    {formatConsumed(entry.consumed)} of {entry.limit}
                  </span>
                </div>
                <div
                  role="meter"
                  aria-label={USAGE_LABELS[entry.resource]}
                  aria-valuemin={0}
                  aria-valuemax={entry.limit}
                  aria-valuenow={entry.consumed}
                  className="h-1.5 overflow-hidden rounded-full bg-secondary"
                >
                  <div
                    className={cn(
                      'h-full',
                      entry.consumed >= entry.limit
                        ? 'bg-destructive'
                        : entry.consumed / entry.limit >= 0.8
                          ? 'bg-warning'
                          : 'bg-foreground',
                    )}
                    style={{ width: `${Math.min(100, (entry.consumed / Math.max(1, entry.limit)) * 100)}%` }}
                  />
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

interface SessionRow {
  id: string
  createdAt: string
  lastSeenAt: string
  userAgent: string | null
  current: boolean
}

/** Where this User is signed in, and a way to sign other devices out. */
function SessionDetails() {
  const sessions = useQuery<SessionRow[], ApiError>({
    queryKey: ['/api/v1/me/sessions'],
    queryFn: async ({ signal }) => readSessions(await listSessionsApiV1MeSessionsGet({ signal })),
    retry: false,
  })
  const revokeOne = useMutation<void, ApiError, string>({
    mutationFn: (sessionId) => revokeOneSessionApiV1MeSessionsSessionIdDelete(sessionId),
    onSuccess: () => void sessions.refetch(),
  })
  const revokeOthers = useMutation<unknown, ApiError>({
    mutationFn: () => revokeOtherSessionsApiV1MeSessionsDelete(),
    onSuccess: () => void sessions.refetch(),
  })

  const others = (sessions.data ?? []).filter((row) => !row.current)

  return (
    <Section
      id="sessions"
      title="Where you are signed in"
      description="Sign out a device you no longer use. This does not affect the device you are using now."
      actions={
        others.length === 0 ? undefined : (
          <Button type="button" variant="outline" size="sm" onClick={() => revokeOthers.mutate()} disabled={revokeOthers.isPending}>
            Sign out other devices
          </Button>
        )
      }
    >
      {sessions.isPending ? (
        <LoadingState label="Loading sessions…" />
      ) : sessions.isError ? (
        <ErrorNotice error={sessions.error} onRetry={() => void sessions.refetch()} />
      ) : (
        <ul aria-label="Sessions" className="divide-y divide-border rounded-lg border">
          {sessions.data.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-small font-medium">{row.userAgent ?? 'Unknown device'}</p>
                <p className="font-mono text-caption text-muted-foreground">
                  Signed in {new Date(row.createdAt).toLocaleDateString()} · last active{' '}
                  {new Date(row.lastSeenAt).toLocaleString()}
                </p>
              </div>
              {row.current ? (
                <StatusBadge tone="success">This device</StatusBadge>
              ) : (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => revokeOne.mutate(row.id)}
                  disabled={revokeOne.isPending}
                >
                  Sign out
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {revokeOne.isError ? <ErrorNotice error={revokeOne.error} /> : null}
      {revokeOthers.isError ? <ErrorNotice error={revokeOthers.error} /> : null}
    </Section>
  )
}

/** Read the untyped sessions body defensively; anything unexpected is left out. */
function readSessions(body: unknown): SessionRow[] {
  if (typeof body !== 'object' || body === null) return []
  const rows = (body as { sessions?: unknown }).sessions
  if (!Array.isArray(rows)) return []
  return rows.flatMap((row): SessionRow[] => {
    if (typeof row !== 'object' || row === null) return []
    const fields = row as Record<string, unknown>
    if (typeof fields['id'] !== 'string') return []
    return [
      {
        id: fields['id'],
        createdAt: typeof fields['createdAt'] === 'string' ? fields['createdAt'] : '',
        lastSeenAt: typeof fields['lastSeenAt'] === 'string' ? fields['lastSeenAt'] : '',
        userAgent: typeof fields['userAgent'] === 'string' ? fields['userAgent'] : null,
        current: fields['current'] === true,
      },
    ]
  })
}

function formatConsumed(consumed: number): string {
  return Number.isInteger(consumed) ? String(consumed) : consumed.toFixed(1)
}
