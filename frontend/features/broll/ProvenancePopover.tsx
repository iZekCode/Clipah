'use client'

import { useState } from 'react'

import type { BrollSuggestionResponseProvenance } from '@/lib/api/generated/model'

/**
 * Where one picture came from, shown on request rather than crowding every card.
 *
 * A member deciding whether to publish footage is entitled to the same evidence a
 * lawyer would ask for a year later: who made it, where it lives, and under what
 * licence. Anything the provenance does not record is left out rather than guessed at.
 */
export function ProvenancePopover({
  provenance,
}: {
  provenance: BrollSuggestionResponseProvenance
}) {
  const [open, setOpen] = useState(false)
  if (provenance === null || provenance === undefined) {
    return null
  }
  return (
    <div className="text-xs">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="underline underline-offset-2"
      >
        Where this came from
      </button>
      {open ? (
        <dl className="mt-1 flex flex-col gap-1 rounded border p-2">
          <div className="flex gap-1">
            <dt className="text-muted-foreground">Provider</dt>
            <dd>{provenance.provider}</dd>
          </div>
          <div className="flex gap-1">
            <dt className="text-muted-foreground">Author</dt>
            <dd>
              {provenance.authorUrl === '' ? (
                provenance.author
              ) : (
                <a href={provenance.authorUrl} rel="noreferrer noopener" target="_blank">
                  {provenance.author}
                </a>
              )}
            </dd>
          </div>
          <div className="flex gap-1">
            <dt className="text-muted-foreground">Licence</dt>
            <dd>
              {provenance.licenseUrl === '' ? (
                provenance.licenseName
              ) : (
                <a href={provenance.licenseUrl} rel="noreferrer noopener" target="_blank">
                  {provenance.licenseName}
                </a>
              )}
            </dd>
          </div>
          {provenance.sourceUrl === '' ? null : (
            <div>
              <a href={provenance.sourceUrl} rel="noreferrer noopener" target="_blank">
                View the original
              </a>
            </div>
          )}
          <p className="text-muted-foreground">{provenance.attributionText}</p>
        </dl>
      ) : null}
    </div>
  )
}
