'use client'

import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Keyboard,
  Repeat,
} from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type SyntheticEvent,
} from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { Poster } from '@/components/media/poster'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { IconButton } from '@/components/ui/icon-button'
import { Switch } from '@/components/ui/switch'
import { ScoreBars } from '@/features/clips/ScoreBars'
import { useOpenEdit } from '@/features/clips/use-open-edit'
import { useProjectCandidates } from '@/features/clips/use-project-candidates'
import { useTranscript } from '@/features/media/use-transcript'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { CandidateResponse, ProxyPlaybackResponse } from '@/lib/api/generated/model'
import { showApiV1ProjectsProjectIdProxyGet } from '@/lib/api/generated/playback/playback'
import { formatClock } from '@/lib/media/time'
import { transcriptWindow } from '@/lib/media/transcript'
import { cn } from '@/lib/utils'

import { useReviewKeys } from './use-review-keys'

const SHORTCUTS = [
  ['Space', 'Play or pause'],
  ['J', 'Next moment'],
  ['K', 'Previous moment'],
  ['E', 'Edit this clip'],
  ['Esc', 'Back to the project'],
  ['?', 'Show these shortcuts'],
] as const

/**
 * A focused theater for deciding which moments to edit.
 *
 * It reads the same candidates the Project page reads, plays exactly one moment at a time,
 * shows the words around it, and keeps the chosen moment in `?moment=` so a link or a refresh
 * lands on the same one. Nothing here is saved: moving on is not a decision.
 */
export function ReviewMode({ projectId }: { projectId: string }) {
  const router = useRouter()
  const { candidates, query: moments } = useProjectCandidates(projectId)
  const ordered = useMemo(
    () => [...candidates].sort((left, right) => left.rank - right.rank),
    [candidates],
  )
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [helpOpen, setHelpOpen] = useState(false)

  useEffect(() => {
    if (ordered.length === 0 || currentId !== null) return
    const requested = new URLSearchParams(window.location.search).get('moment')
    setCurrentId(ordered.some((entry) => entry.id === requested) ? requested : ordered[0]!.id)
  }, [ordered, currentId])

  const index = ordered.findIndex((entry) => entry.id === currentId)
  const current = index === -1 ? null : ordered[index]!

  function go(nextIndex: number): void {
    const next = ordered[nextIndex]
    if (next === undefined) return
    setCurrentId(next.id)
    const url = new URL(window.location.href)
    url.searchParams.set('moment', next.id)
    window.history.replaceState(window.history.state, '', url.toString())
  }

  const player = useRef<{ toggle: () => void } | null>(null)
  // Before a moment is chosen the placeholder is never opened: `onEdit` waits for one.
  const edit = useOpenEdit(current ?? { id: '', projectId })

  useReviewKeys({
    onPlayPause: () => player.current?.toggle(),
    onNext: () => go(index + 1),
    onPrevious: () => go(index - 1),
    onEdit: () => (current === null ? undefined : edit.open()),
    onExit: () => router.push(`/dashboard/projects/${projectId}`),
    onHelp: () => setHelpOpen(true),
  })

  if (moments.isPending || moments.hasNextPage) {
    return <LoadingState label="Loading moments…" variant="cards" count={1} />
  }
  if (moments.isError) {
    if (moments.error.status === 404) {
      return <NothingToReview projectId={projectId} />
    }
    return <ErrorNotice error={moments.error} onRetry={() => void moments.refetch()} />
  }
  if (ordered.length === 0) {
    return <NothingToReview projectId={projectId} />
  }
  if (current === null) {
    return <LoadingState label="Loading moments…" variant="cards" count={1} />
  }

  return (
    <div className="-mx-4 -my-6 min-h-[calc(100vh-52px)] bg-stage px-4 py-4 sm:-mx-6 sm:px-6 lg:-my-8">
      <div className="mb-4 flex items-center justify-between gap-3">
        <Link
          href={`/dashboard/projects/${projectId}`}
          className="inline-flex items-center gap-1.5 text-small font-medium text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft aria-hidden="true" strokeWidth={1.75} className="size-4" />
          Back to project
        </Link>
        <p className="tabular font-mono text-caption text-subtle-foreground">
          {index + 1} / {ordered.length}
        </p>
        <IconButton
          label="Review shortcuts"
          shortcut="?"
          icon={<Keyboard strokeWidth={1.75} />}
          onClick={() => setHelpOpen(true)}
        />
      </div>

      <div className="grid gap-6 md:grid-cols-[200px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,420px)_minmax(0,1fr)]">
        <nav aria-label="Moments" className="hidden md:block">
          <ol className="space-y-2">
            {ordered.map((entry, position) => (
              <li key={entry.id}>
                <button
                  type="button"
                  aria-current={entry.id === current.id ? 'true' : undefined}
                  onClick={() => go(position)}
                  className={cn(
                    'grid w-full grid-cols-[56px_minmax(0,1fr)] gap-2 rounded-md border p-1.5 text-left transition-colors duration-fast ease-signal',
                    entry.id === current.id
                      ? 'border-primary bg-card'
                      : 'border-transparent hover:bg-card',
                  )}
                >
                  <span className="relative block aspect-[9/16] overflow-hidden rounded-sm">
                    <Poster
                      projectId={projectId}
                      startMs={entry.startMs}
                      endMs={entry.endMs}
                      aspect="portrait"
                    />
                  </span>
                  <span className="min-w-0 space-y-0.5">
                    <span className="block font-mono text-caption text-primary">#{entry.rank}</span>
                    <span className="line-clamp-2 block text-caption">{entry.hook}</span>
                    <span className="block font-mono text-caption text-subtle-foreground">
                      {formatClock(entry.durationMs)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <div className="space-y-3">
          <MomentPlayer ref={player} projectId={projectId} moment={current} />
          <div className="flex items-center justify-between gap-2 md:hidden">
            <Button
              variant="secondary"
              onClick={() => go(index - 1)}
              disabled={index <= 0}
              aria-label="Previous moment"
            >
              <ChevronLeft aria-hidden="true" strokeWidth={1.75} /> Previous
            </Button>
            <Button
              variant="secondary"
              onClick={() => go(index + 1)}
              disabled={index >= ordered.length - 1}
              aria-label="Next moment"
            >
              Next <ChevronRight aria-hidden="true" strokeWidth={1.75} />
            </Button>
          </div>
        </div>

        <MomentDetail
          projectId={projectId}
          moment={current}
          onEdit={() => edit.open()}
          editing={edit.isPending}
          error={edit.error}
        />
      </div>

      <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
        <DialogContent aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>Review shortcuts</DialogTitle>
          </DialogHeader>
          <dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-4 gap-y-2">
            {SHORTCUTS.map(([key, action]) => (
              <div key={key} className="contents">
                <dt>
                  <kbd className="rounded-sm border border-line-strong px-1.5 py-0.5 font-mono text-caption">
                    {key}
                  </kbd>
                </dt>
                <dd className="text-small">{action}</dd>
              </div>
            ))}
          </dl>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function NothingToReview({ projectId }: { projectId: string }) {
  return (
    <EmptyState
      title="No clips to review yet"
      description="Suggested moments appear here once the video has been transcribed and analysed."
      action={
        <Link
          href={`/dashboard/projects/${projectId}`}
          className="text-small font-semibold text-primary hover:underline"
        >
          Back to project
        </Link>
      }
    />
  )
}

/** The moment's range from the proxy, bounded, with an optional loop. */
const MomentPlayer = forwardRef<
  { toggle: () => void },
  { projectId: string; moment: CandidateResponse }
>(function MomentPlayer({ projectId, moment }, ref) {
  const { active } = useWorkspaceScope()
  const video = useRef<HTMLVideoElement>(null)
  const [loop, setLoop] = useState(false)
  const playback = useQuery<ProxyPlaybackResponse, ApiError>({
    queryKey: ['/api/v1/projects/proxy', active.id, projectId],
    queryFn: ({ signal }) =>
      showApiV1ProjectsProjectIdProxyGet(projectId, { workspace_id: active.id }, { signal }),
    retry: false,
    gcTime: 0,
    staleTime: 0,
  })

  useImperativeHandle(ref, () => ({
    toggle: () => {
      const element = video.current
      if (element === null) return
      if (element.paused) void element.play()
      else element.pause()
    },
  }))

  // Moving to another moment moves the player to its start once the media is loaded.
  useEffect(() => {
    if (video.current !== null && video.current.readyState > 0) {
      video.current.currentTime = moment.startMs / 1000
    }
  }, [moment.id, moment.startMs])

  /** Start at the moment rather than at the beginning of the source. */
  function start(event: SyntheticEvent<HTMLVideoElement>): void {
    event.currentTarget.currentTime = moment.startMs / 1000
    void event.currentTarget.play()
  }

  /** Stop where the moment ends, or go round again when looping. */
  function bound(event: SyntheticEvent<HTMLVideoElement>): void {
    const element = event.currentTarget
    if (element.currentTime >= moment.endMs / 1000) {
      element.currentTime = moment.startMs / 1000
      if (!loop) element.pause()
    }
  }

  return (
    <div className="space-y-2">
      <div className="relative mx-auto aspect-[9/16] max-h-[calc(100vh-10rem)] overflow-hidden rounded-lg bg-background">
        {playback.data === undefined ? (
          playback.isError ? (
            <ErrorNotice error={playback.error} />
          ) : null
        ) : (
          <video
            ref={video}
            data-testid="review-video"
            src={playback.data.url}
            playsInline
            preload="metadata"
            onLoadedMetadata={start}
            onTimeUpdate={bound}
            onClick={(event) => {
              const element = event.currentTarget
              if (element.paused) void element.play()
              else element.pause()
            }}
            className="absolute inset-0 size-full object-cover"
          />
        )}
      </div>
      <label className="flex items-center justify-center gap-2 text-caption text-muted-foreground">
        <Repeat aria-hidden="true" strokeWidth={1.75} className="size-3.5" />
        Loop
        <Switch checked={loop} onCheckedChange={setLoop} aria-label="Loop this moment" />
      </label>
    </div>
  )
})

/** Hook, reason, warnings, words in context, and the score, for the chosen moment. */
function MomentDetail({
  projectId,
  moment,
  onEdit,
  editing,
  error,
}: {
  projectId: string
  moment: CandidateResponse
  onEdit: () => void
  editing: boolean
  error: ApiError | null
}) {
  const transcript = useTranscript(projectId, { enabled: true })
  const words = transcript.data?.words ?? []
  const context = transcriptWindow(words, moment.startMs, moment.endMs)

  return (
    <section aria-label="Moment" className="space-y-5 md:col-span-2 xl:col-span-1">
      <div className="space-y-2">
        <p className="font-mono text-caption text-primary">
          #{moment.rank} · {formatClock(moment.startMs)}–{formatClock(moment.endMs)} ·{' '}
          {Math.round(moment.score * 100)}
        </p>
        <h1 className="font-display text-h1 lg:text-display">{moment.hook}</h1>
        <p className="text-body text-muted-foreground">{moment.reason}</p>
      </div>
      {moment.contextWarnings.length === 0 ? null : (
        <div
          role="group"
          aria-label="Context warnings"
          className="flex gap-2 rounded-md bg-warning-soft p-3 text-small text-warning"
        >
          <AlertTriangle aria-hidden="true" strokeWidth={1.75} className="mt-0.5 size-4 shrink-0" />
          <ul className="space-y-1">
            {moment.contextWarnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
      <div
        role="group"
        aria-label="Transcript"
        className="space-y-1 rounded-lg border bg-card p-4 text-small leading-relaxed"
      >
        {words.length === 0 ? (
          <p className="text-muted-foreground">{moment.transcriptExcerpt}</p>
        ) : (
          <p>
            {context.before === '' ? null : (
              <span className="text-subtle-foreground">{context.before}</span>
            )}{' '}
            <span className="text-foreground">{context.inside}</span>{' '}
            {context.after === '' ? null : (
              <span className="text-subtle-foreground">{context.after}</span>
            )}
          </p>
        )}
      </div>
      <ScoreBars breakdown={moment.scoreBreakdown} />
      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" loading={editing} onClick={onEdit}>
          Edit clip
        </Button>
        <span className="text-caption text-subtle-foreground">
          or press <kbd className="font-mono">E</kbd>
        </span>
      </div>
      {error === null ? null : <ErrorNotice error={error} />}
    </section>
  )
}
