'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useMemo, useState } from 'react'

import { ErrorNotice } from '@/components/error-notice'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import { listCollectionApiV1ProjectsGet } from '@/lib/api/generated/projects/projects'
import { searchApiV1SearchGet } from '@/lib/api/generated/search/search'
import type {
  ExportState,
  ProjectPageResponse,
  SearchEntityType,
  SearchLanguage,
  SearchPageResponse,
  SearchResultResponse,
} from '@/lib/api/generated/model'

const PAGE_SIZE = 20

/** What each kind of result is called on the screen a member is reading. */
const TYPE_LABELS: Record<SearchEntityType, string> = {
  project: 'Project',
  transcript: 'Transcript',
  clip: 'Clip',
  campaign_output: 'Campaign copy',
}

/** The languages this library stems, plus the honest name for everything else. */
const LANGUAGE_LABELS: Record<SearchLanguage, string> = {
  id: 'Indonesian',
  en: 'English',
  other: 'Other',
}

/** One member's narrowing of the library, before any of it is sent anywhere. */
interface Filters {
  types: SearchEntityType[]
  projectId: string
  speaker: string
  topic: string
  language: SearchLanguage | ''
  exportState: ExportState | ''
  from: string
  to: string
}

/**
 * Search everything this Workspace has already made, by the words that were said in it.
 *
 * Nothing is asked of the backend until the member has typed something worth asking: an
 * empty question has no answer, and a library that queries itself on every keystroke of
 * an empty box is spending a member's Workspace budget on nothing. Every result is
 * rendered as React children, because all of it — transcript words, model-written hooks,
 * campaign copy — was written by somebody other than this application.
 */
export function LibrarySearch({
  defaultTypes = [],
  projectId,
  heading = 'Search this Workspace',
  description = 'Find a Project, a moment in a transcript, a clip, or the copy written for it.',
}: {
  defaultTypes?: SearchEntityType[]
  projectId?: string
  heading?: string
  description?: string
} = {}) {
  const { active } = useWorkspaceScope()
  const [question, setQuestion] = useState('')
  const [filters, setFilters] = useState<Filters>({
    types: defaultTypes,
    projectId: '',
    speaker: '',
    topic: '',
    language: '',
    exportState: '',
    from: '',
    to: '',
  })

  const asked = question.trim()
  const parameters = useMemo(
    () => ({
      workspace_id: active.id,
      q: asked,
      limit: PAGE_SIZE,
      ...(filters.types.length > 0 ? { type: filters.types } : {}),
      ...(projectId === undefined
        ? filters.projectId === ''
          ? {}
          : { projectId: filters.projectId }
        : { projectId }),
      ...(filters.speaker === '' ? {} : { speaker: filters.speaker }),
      ...(filters.topic === '' ? {} : { topic: filters.topic }),
      ...(filters.language === '' ? {} : { language: filters.language }),
      ...(filters.exportState === '' ? {} : { exportState: filters.exportState }),
      ...(filters.from === '' ? {} : { createdAfter: startOfDay(filters.from) }),
      ...(filters.to === '' ? {} : { createdBefore: endOfDay(filters.to) }),
    }),
    [active.id, asked, filters, projectId],
  )

  const found = useInfiniteQuery<SearchPageResponse, ApiError>({
    queryKey: ['/api/v1/search', parameters],
    enabled: asked !== '',
    queryFn: ({ pageParam, signal }) =>
      searchApiV1SearchGet(
        { ...parameters, ...(typeof pageParam === 'string' ? { cursor: pageParam } : {}) },
        { signal },
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    retry: false,
  })

  const results = useMemo(
    () => (found.data?.pages ?? []).flatMap((page) => page.results),
    [found.data],
  )

  return (
    <section className="space-y-4">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold tracking-tight">{heading}</h2>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>

      <label className="block space-y-1">
        <span className="text-sm font-medium">Search the library</span>
        <input
          type="search"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="A word, a name, or a &quot;quoted phrase&quot;"
          className="w-full rounded-md border px-3 py-2 text-sm"
        />
      </label>

      <SearchFilters
        filters={filters}
        pinnedToOneProject={projectId !== undefined}
        onChange={(change) => setFilters((current) => ({ ...current, ...change }))}
      />

      {asked === '' ? (
        <p className="text-sm text-muted-foreground">
          Type a word somebody said, or the name of a Project.
        </p>
      ) : found.isError ? (
        <ErrorNotice error={found.error} />
      ) : found.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Searching…
        </p>
      ) : results.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing in this Workspace matches that yet.
        </p>
      ) : (
        <>
          <ul aria-label="Search results" className="space-y-3">
            {results.map((result) => (
              <li key={result.id} className="rounded-lg border p-4">
                <SearchResult result={result} />
              </li>
            ))}
          </ul>
          {found.hasNextPage ? (
            <button
              type="button"
              onClick={() => void found.fetchNextPage()}
              disabled={found.isFetchingNextPage}
              className="rounded-md border px-3 py-2 text-sm"
            >
              {found.isFetchingNextPage ? 'Loading…' : 'More results'}
            </button>
          ) : null}
        </>
      )}
    </section>
  )
}

/** The narrowings a member reaches for: what kind of work, whose voice, and when. */
function SearchFilters({
  filters,
  pinnedToOneProject,
  onChange,
}: {
  filters: Filters
  pinnedToOneProject: boolean
  onChange: (change: Partial<Filters>) => void
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {pinnedToOneProject ? null : (
        <ProjectFilter
          selected={filters.projectId}
          onSelect={(selection) => onChange({ projectId: selection })}
        />
      )}
      <label className="space-y-1 text-sm">
        <span className="font-medium">Content type</span>
        <select
          value={filters.types[0] ?? ''}
          onChange={(event) =>
            onChange({
              types: event.target.value === '' ? [] : [event.target.value as SearchEntityType],
            })
          }
          className="w-full rounded-md border px-2 py-1"
        >
          <option value="">Everything</option>
          {(Object.keys(TYPE_LABELS) as SearchEntityType[]).map((type) => (
            <option key={type} value={type}>
              {TYPE_LABELS[type]}
            </option>
          ))}
        </select>
      </label>

      <label className="space-y-1 text-sm">
        <span className="font-medium">Language</span>
        <select
          value={filters.language}
          onChange={(event) => onChange({ language: event.target.value as SearchLanguage | '' })}
          className="w-full rounded-md border px-2 py-1"
        >
          <option value="">Any language</option>
          {(Object.keys(LANGUAGE_LABELS) as SearchLanguage[]).map((language) => (
            <option key={language} value={language}>
              {LANGUAGE_LABELS[language]}
            </option>
          ))}
        </select>
      </label>

      <label className="space-y-1 text-sm">
        <span className="font-medium">Export state</span>
        <select
          value={filters.exportState}
          onChange={(event) => onChange({ exportState: event.target.value as ExportState | '' })}
          className="w-full rounded-md border px-2 py-1"
        >
          <option value="">Exported or not</option>
          <option value="exported">Exported</option>
          <option value="not_exported">Not exported</option>
        </select>
      </label>

      <label className="space-y-1 text-sm">
        <span className="font-medium">Speaker or guest</span>
        <input
          value={filters.speaker}
          onChange={(event) => onChange({ speaker: event.target.value })}
          className="w-full rounded-md border px-2 py-1"
        />
      </label>

      <label className="space-y-1 text-sm">
        <span className="font-medium">Topic</span>
        <input
          value={filters.topic}
          onChange={(event) => onChange({ topic: event.target.value })}
          className="w-full rounded-md border px-2 py-1"
        />
      </label>

      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-1 text-sm">
          <span className="font-medium">From</span>
          <input
            type="date"
            value={filters.from}
            onChange={(event) => onChange({ from: event.target.value })}
            className="w-full rounded-md border px-2 py-1"
          />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium">To</span>
          <input
            type="date"
            value={filters.to}
            onChange={(event) => onChange({ to: event.target.value })}
            className="w-full rounded-md border px-2 py-1"
          />
        </label>
      </div>
    </div>
  )
}

/**
 * Narrow the library to one Project.
 *
 * The list is the member's own visible Projects, read the same way the Project library
 * reads them, so a Project they may not see is not even offered as a choice.
 */
function ProjectFilter({
  selected,
  onSelect,
}: {
  selected: string
  onSelect: (projectId: string) => void
}) {
  const { active } = useWorkspaceScope()
  const projects = useQuery<ProjectPageResponse, ApiError>({
    queryKey: ['/api/v1/projects', active.id, 'search-filter'],
    queryFn: ({ signal }) =>
      listCollectionApiV1ProjectsGet({ workspace_id: active.id, limit: 100 }, { signal }),
    retry: false,
  })

  return (
    <label className="space-y-1 text-sm">
      <span className="font-medium">Project</span>
      <select
        value={selected}
        onChange={(event) => onSelect(event.target.value)}
        className="w-full rounded-md border px-2 py-1"
      >
        <option value="">Every Project</option>
        {(projects.data?.projects ?? []).map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
    </label>
  )
}

/** One result: what it is, where it came from, and the words that answered the question. */
function SearchResult({ result }: { result: SearchResultResponse }) {
  return (
    <article className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-2 text-xs text-muted-foreground">
        <span className="rounded-full border px-2 py-0.5">{TYPE_LABELS[result.type]}</span>
        <span>{result.projectName}</span>
        {result.speaker === null ? null : <span>{result.speaker}</span>}
        {result.startMs === null ? null : <span>{timecode(result.startMs)}</span>}
        {result.exportState === 'exported' ? <span>Exported</span> : null}
      </div>
      <Link href={result.deepLink} className="block font-medium hover:underline">
        {result.title}
      </Link>
      <p className="text-sm text-muted-foreground">
        {result.fragments.map((fragment, index) =>
          fragment.highlighted ? (
            <mark key={index} className="bg-transparent font-semibold text-foreground">
              {fragment.text}
            </mark>
          ) : (
            <span key={index}>{fragment.text}</span>
          ),
        )}
      </p>
      {result.topics.length === 0 ? null : (
        <ul className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          {result.topics.map((topic) => (
            <li key={topic} className="rounded-full border px-2 py-0.5">
              {topic}
            </li>
          ))}
        </ul>
      )}
    </article>
  )
}

/** Read a millisecond offset the way a person reads a player's clock. */
function timecode(milliseconds: number): string {
  const total = Math.floor(milliseconds / 1000)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

/** Read a date control as the instant its day begins, in UTC, as the backend stores time. */
function startOfDay(day: string): string {
  return new Date(`${day}T00:00:00.000Z`).toISOString()
}

/** Read a date control as the last instant of its day, so "to" includes that day. */
function endOfDay(day: string): string {
  return new Date(`${day}T23:59:59.999Z`).toISOString()
}
