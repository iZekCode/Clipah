'use client'

import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import type { ApiError } from '@/lib/api/client'
import {
  createEvidenceApiV1ProjectsProjectIdCandidatesCandidateIdClaimEvidencePost,
  listCollectionApiV1ProjectsProjectIdCandidatesCandidateIdClaimEvidenceGet,
} from '@/lib/api/generated/claim-evidence/claim-evidence'
import type {
  ClaimEvidenceListResponse,
  ClaimEvidenceResponse,
} from '@/lib/api/generated/model'

/** What each status means to a reader, stated so nothing implies Clipah checked it. */
const STATUS_COPY: Record<string, string> = {
  unverified: 'Not verified — a member recorded this source; Clipah has not checked it.',
  supported: 'A member marked this source as supporting the claim.',
  disputed: 'A member marked this source as disputing the claim.',
  retracted: 'A member marked this source as retracted.',
}

/** One blank citation, in the shape the form collects it. */
const EMPTY = {
  claimText: '',
  sourceUrl: '',
  sourceTitle: '',
  publisher: '',
  startWordId: '',
  endWordId: '',
}

/**
 * Attach and review the sources behind claims a clip makes.
 *
 * Nothing here verifies anything. A citation is what a member said about a source, so it
 * is shown as their assertion, its link opens isolated, and every field — including one
 * containing markup — is rendered as text.
 */
export function EvidencePanel({
  projectId,
  candidateId,
  workspaceId,
}: {
  projectId: string
  candidateId: string
  workspaceId: string
}) {
  const [draft, setDraft] = useState({ ...EMPTY })
  const [working, setWorking] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const evidence = useQuery<ClaimEvidenceListResponse, ApiError>({
    queryKey: ['/api/v1/claim-evidence', workspaceId, projectId, candidateId],
    queryFn: ({ signal }) =>
      listCollectionApiV1ProjectsProjectIdCandidatesCandidateIdClaimEvidenceGet(
        projectId,
        candidateId,
        { workspace_id: workspaceId },
        { signal },
      ),
    retry: false,
  })

  const attach = useCallback(async () => {
    setWorking(true)
    setRefusal(null)
    setFailure(null)
    try {
      await createEvidenceApiV1ProjectsProjectIdCandidatesCandidateIdClaimEvidencePost(
        projectId,
        candidateId,
        { ...draft, retrievedAt: new Date().toISOString() },
        { workspace_id: workspaceId },
      )
      setDraft({ ...EMPTY })
      await evidence.refetch()
    } catch (error) {
      const refused = error as ApiError
      if (refused.code === 'EVIDENCE_INVALID') {
        setRefusal(
          'That source could not be accepted. Use an https link with no login details, and plain text without markup.',
        )
      } else {
        setFailure(refused)
      }
    } finally {
      setWorking(false)
    }
  }, [candidateId, draft, evidence, projectId, workspaceId])

  const found = evidence.data?.evidence ?? []

  return (
    <section aria-label="Claim evidence" className="flex flex-col gap-3 text-xs">
      <header>
        <h2 className="text-sm font-semibold">Sources</h2>
        <p className="text-muted-foreground">
          Record where a claim in this clip came from. Clipah stores what you say about a
          source; it never decides whether the claim is true.
        </p>
      </header>

      {found.length === 0 ? (
        <p className="text-muted-foreground">No sources recorded for this clip yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {found.map((item) => (
            <li key={item.id} className="flex flex-col gap-1 rounded border p-2">
              <p className="font-medium">{item.claimText}</p>
              <p>
                <a
                  href={item.sourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline"
                >
                  {item.sourceTitle}
                </a>{' '}
                <span className="text-muted-foreground">· {item.publisher}</span>
              </p>
              <p className="text-muted-foreground">{statusCopy(item)}</p>
              <p className="text-muted-foreground">
                Words {item.startWordId}–{item.endWordId}
              </p>
            </li>
          ))}
        </ul>
      )}

      {refusal === null ? null : (
        <p role="status" className="rounded border p-2">
          {refusal}
        </p>
      )}
      {failure === null ? null : <ErrorNotice error={failure} />}

      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          void attach()
        }}
      >
        <Field label="Claim" value={draft.claimText} onChange={(v) => setDraft({ ...draft, claimText: v })} />
        <Field label="Source link" value={draft.sourceUrl} onChange={(v) => setDraft({ ...draft, sourceUrl: v })} />
        <Field label="Title" value={draft.sourceTitle} onChange={(v) => setDraft({ ...draft, sourceTitle: v })} />
        <Field label="Publisher" value={draft.publisher} onChange={(v) => setDraft({ ...draft, publisher: v })} />
        <Field label="First word" value={draft.startWordId} onChange={(v) => setDraft({ ...draft, startWordId: v })} />
        <Field label="Last word" value={draft.endWordId} onChange={(v) => setDraft({ ...draft, endWordId: v })} />
        <button type="submit" disabled={working} className="w-fit rounded border px-2 py-1">
          Attach source
        </button>
      </form>
    </section>
  )
}

/** One labelled text input, so every field is reachable by its own name. */
function Field({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-muted-foreground">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded border px-2 py-1"
      />
    </label>
  )
}

/** Say what this status means without implying anybody checked the claim. */
function statusCopy(evidence: ClaimEvidenceResponse): string {
  return (
    STATUS_COPY[evidence.verificationStatus] ??
    'Not verified — a member recorded this source; Clipah has not checked it.'
  )
}
