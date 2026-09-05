'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { readCurrentUserApiV1MeGet } from '@/lib/api/generated/auth/auth'
import {
  createApiV1SourceConnectionsPost,
  indexApiV1SourceConnectionsGet,
  revokeApiV1SourceConnectionsConnectionIdDelete,
} from '@/lib/api/generated/source-connections/source-connections'
import type {
  CurrentUserResponse,
  SourceConnectionResponse,
  SourceConnectionsResponse,
} from '@/lib/api/generated/model'

/**
 * Connect a YouTube account by handing Clipah the member's own cookie jar.
 *
 * This is the most dangerous thing the product asks anybody to do, so the dialog is
 * mostly words. It says what a cookie jar is, what happens to a YouTube account that is
 * treated as automated, how long the credential is kept, what ends it early, and that it
 * may only be used for an account the member owns. Nothing is sent until they confirm
 * both that they understand and that the account is theirs.
 *
 * It renders nothing at all where the server has not switched the capability on, so a
 * deployment without the security review behind it offers no way to start.
 *
 * There is deliberately no control that reads cookies out of a browser profile: on a
 * hosted worker that profile belongs to the machine rather than to the member.
 */
export function YouTubeConnectionDialog() {
  const { active } = useWorkspaceScope()
  const queryClient = useQueryClient()
  const fileId = useId()
  const [jar, setJar] = useState<string | null>(null)
  const [understood, setUnderstood] = useState(false)
  const [owned, setOwned] = useState(false)

  const me = useQuery<CurrentUserResponse, ApiError>({
    queryKey: ['/api/v1/me'],
    queryFn: ({ signal }) => readCurrentUserApiV1MeGet({ signal }),
    retry: false,
  })
  const enabled = me.data?.capabilities.authenticatedYoutubeImport === true

  const connections = useQuery<SourceConnectionsResponse, ApiError>({
    queryKey: ['/api/v1/source-connections', active.id],
    enabled,
    queryFn: ({ signal }) => indexApiV1SourceConnectionsGet({ workspace_id: active.id }, { signal }),
    retry: false,
  })

  const connect = useMutation<SourceConnectionResponse, ApiError, string>({
    mutationFn: (cookiesBase64) =>
      createApiV1SourceConnectionsPost(
        { cookiesBase64, consentAcknowledged: true, ownershipAttested: true },
        { workspace_id: active.id },
      ),
    onSuccess: async () => {
      setJar(null)
      setUnderstood(false)
      setOwned(false)
      await queryClient.invalidateQueries({ queryKey: ['/api/v1/source-connections', active.id] })
    },
  })

  const revoke = useMutation<void, ApiError, string>({
    mutationFn: async (connectionId) => {
      await revokeApiV1SourceConnectionsConnectionIdDelete(connectionId, {
        workspace_id: active.id,
      })
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['/api/v1/source-connections', active.id] })
    },
  })

  if (!enabled) {
    return null
  }

  const ready = jar !== null && understood && owned

  return (
    <section
      aria-label="YouTube account"
      className="flex flex-col gap-3 rounded-lg border p-4 text-sm"
    >
      <h3 className="font-medium">Connect a YouTube account</h3>

      <div className="flex flex-col gap-2 text-xs text-muted-foreground">
        <p>
          A cookie file is your signed-in session for YouTube. Anyone holding it can act as
          you on that account until it stops working, which is why Clipah keeps it
          encrypted, uses it only for the imports you ask for, and never shows it back to
          you or to anyone else in this Workspace.
        </p>
        <p>
          Google may restrict or ban an account whose session is used by automated tools.
          Only connect an account you are willing to take that risk with.
        </p>
        <p>
          The connection is kept for at most seven days, and for less than that when your
          own cookies expire sooner. If you sign out of YouTube in the browser you
          exported from, the connection stops working immediately, and you can revoke it
          here at any time.
        </p>
        <p>
          Only connect your own account. Uploading somebody else&apos;s session is not
          something this feature supports.
        </p>
      </div>

      <label className="flex flex-col gap-1 text-xs" htmlFor={fileId}>
        Cookie file (Netscape format, exported from your own browser)
        <input
          id={fileId}
          type="file"
          accept=".txt,text/plain"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0]
            if (file === undefined) {
              setJar(null)
              return
            }
            void readAsText(file).then((contents) => setJar(encode(contents)))
          }}
          className="rounded border px-2 py-1"
        />
      </label>

      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={understood}
          onChange={(event) => setUnderstood(event.currentTarget.checked)}
        />
        I understand that this uploads a live sign-in session and that the account may be
        restricted.
      </label>
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={owned}
          onChange={(event) => setOwned(event.currentTarget.checked)}
        />
        This is my own account, and I have the right to use the videos I import.
      </label>

      <button
        type="button"
        disabled={!ready || connect.isPending}
        onClick={() => (jar === null ? undefined : connect.mutate(jar))}
        className="self-start rounded border px-3 py-1 disabled:opacity-50"
      >
        Connect account
      </button>

      {connect.isError ? <ErrorNotice error={connect.error} /> : null}
      {revoke.isError ? <ErrorNotice error={revoke.error} /> : null}

      <ul className="flex flex-col gap-2">
        {(connections.data?.connections ?? []).map((entry) => (
          <li key={entry.id} className="flex flex-wrap items-center gap-2 rounded border p-2 text-xs">
            <span className="font-medium">{entry.label}</span>
            <span className="text-muted-foreground">{entry.domainScope}</span>
            <span className="text-muted-foreground">
              {entry.status === 'active'
                ? `Expires ${new Date(entry.expiresAt).toLocaleString()}`
                : entry.status}
            </span>
            {entry.status === 'active' ? (
              <button
                type="button"
                onClick={() => revoke.mutate(entry.id)}
                className="ml-auto rounded border px-2 py-1"
              >
                Revoke
              </button>
            ) : null}
          </li>
        ))}
      </ul>

      {connections.isSuccess && connections.data.connections.length === 0 ? (
        <p className="text-xs text-muted-foreground">No account is connected.</p>
      ) : null}
    </section>
  )
}

/**
 * Read one chosen file as text.
 *
 * `FileReader` rather than `Blob.text()`, because the credential has to be readable in
 * every browser this product supports, including the ones without the newer method.
 */
function readAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error ?? new Error('the file could not be read'))
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '')
    reader.readAsText(file)
  })
}

/** Encode one cookie file for transport without ever putting it in a URL or a log. */
function encode(contents: string): string {
  const bytes = new TextEncoder().encode(contents)
  let binary = ''
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }
  return btoa(binary)
}
