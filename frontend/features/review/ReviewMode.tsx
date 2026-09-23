'use client'

import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowLeft,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Keyboard,
  Pause,
  Play,
  Repeat,
  Volume2,
  VolumeX,
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
  type ReactNode,
  type SyntheticEvent,
} from 'react'

import { EmptyState } from '@/components/empty-state'
import { ErrorNotice } from '@/components/error-notice'
import { LoadingState } from '@/components/loading-state'
import { Poster } from '@/components/media/poster'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { IconButton } from '@/components/ui/icon-button'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { ClipSections } from '@/features/clips/ClipSections'
import { LookSelection } from '@/features/clips/LookSelection'
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
  const { active } = useWorkspaceScope()
  const { candidates, query: moments } = useProjectCandidates(projectId)
  const ordered = useMemo(
    () => [...candidates].sort((left, right) => left.rank - right.rank),
    [candidates],
  )
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [helpOpen, setHelpOpen] = useState(false)
  // The look and brand a clip is opened with; kept while moving between moments.
  const [templateId, setTemplateId] = useState('')
  const [brandKitId, setBrandKitId] = useState('')

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
  const openEdit = () =>
    edit.open({
      templateId: templateId === '' ? null : templateId,
      brandKitId: brandKitId === '' ? null : brandKitId,
    })

  useReviewKeys({
    onPlayPause: () => player.current?.toggle(),
    onNext: () => go(index + 1),
    onPrevious: () => go(index - 1),
    onEdit: () => (current === null ? undefined : openEdit()),
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
          onEdit={openEdit}
          editing={edit.isPending}
          error={edit.error}
          look={
            <LookSelection
              workspaceId={active.id}
              templateId={templateId}
              brandKitId={brandKitId}
              onTemplate={setTemplateId}
              onBrandKit={setBrandKitId}
            />
          }
        />
      </div>

      <section aria-label="Clip" className="mt-8">
        {/* Keyed by moment so each one resolves its own Edit and exports. */}
        <ClipSections key={current.id} candidateId={current.id} />
      </section>

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
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(1)
  const [position, setPosition] = useState(moment.startMs)
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
    setPosition(moment.startMs)
    if (video.current !== null && video.current.readyState > 0) {
      video.current.currentTime = moment.startMs / 1000
    }
  }, [moment.id, moment.startMs])

  /** Play or pause, the same action as a click on the picture or the space bar. */
  function togglePlayback(): void {
    const element = video.current
    if (element === null) return
    if (element.paused) void element.play()
    else element.pause()
  }

  /** Scrub inside the moment; the slider never leaves its range. */
  function seek(ms: number): void {
    if (video.current !== null) video.current.currentTime = ms / 1000
    setPosition(ms)
  }

  function changeVolume(next: number): void {
    const element = video.current
    if (element === null) return
    element.volume = next
    element.muted = next === 0
  }

  function toggleMute(): void {
    const element = video.current
    if (element === null) return
    if (element.muted || element.volume === 0) {
      element.muted = false
      if (element.volume === 0) element.volume = 1
    } else {
      element.muted = true
    }
  }

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
    setPosition(Math.min(moment.endMs, Math.max(moment.startMs, element.currentTime * 1000)))
  }

  /** Keep the controls honest when the picture, a key, or the slider changes the player. */
  function syncVolume(event: SyntheticEvent<HTMLVideoElement>): void {
    setMuted(event.currentTarget.muted)
    setVolume(event.currentTarget.volume)
  }

  const silent = muted || volume === 0

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
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onVolumeChange={syncVolume}
            onClick={(event) => {
              const element = event.currentTarget
              if (element.paused) void element.play()
              else element.pause()
            }}
            className="absolute inset-0 size-full object-cover"
          />
        )}
      </div>
      <div className="mx-auto flex items-center gap-2" data-testid="review-controls">
        <IconButton
          size="sm"
          label={playing ? 'Pause' : 'Play'}
          shortcut="Space"
          tooltipSide="top"
          disabled={playback.data === undefined}
          onClick={togglePlayback}
          icon={playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
        />
        <Slider
          aria-label="Position in this moment"
          min={moment.startMs}
          max={moment.endMs}
          step={100}
          value={position}
          disabled={playback.data === undefined}
          onChange={(event) => seek(Number(event.target.value))}
          className="min-w-0 flex-1"
        />
        <span className="shrink-0 font-mono text-caption tabular-nums text-muted-foreground">
          {formatClock(position - moment.startMs)} / {formatClock(moment.endMs - moment.startMs)}
        </span>
        <IconButton
          size="sm"
          label={silent ? 'Unmute' : 'Mute'}
          tooltipSide="top"
          disabled={playback.data === undefined}
          onClick={toggleMute}
          icon={silent ? <VolumeX aria-hidden="true" /> : <Volume2 aria-hidden="true" />}
        />
        <Slider
          aria-label="Volume"
          min={0}
          max={1}
          step={0.05}
          value={silent ? 0 : volume}
          disabled={playback.data === undefined}
          onChange={(event) => changeVolume(Number(event.target.value))}
          className="w-20 shrink-0"
        />
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
  look,
}: {
  projectId: string
  moment: CandidateResponse
  onEdit: () => void
  editing: boolean
  error: ApiError | null
  look: ReactNode
}) {
  const transcript = useTranscript(projectId, { enabled: true })
  const words = transcript.data?.words ?? []
  const context = transcriptWindow(words, moment.startMs, moment.endMs)
  // Shown by default: the evidence is what review is for. It folds away for a long run of moments.
  const [whyOpen, setWhyOpen] = useState(true)

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
      <WhyMoment moment={moment} open={whyOpen} onToggle={() => setWhyOpen((value) => !value)} />
      <ScoreBars breakdown={moment.scoreBreakdown} />
      <div className="flex flex-wrap items-center gap-3">{look}</div>
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

/** Everything the analysis recorded about why this moment was proposed, beyond the one-line reason. */
function WhyMoment({
  moment,
  open,
  onToggle,
}: {
  moment: CandidateResponse
  open: boolean
  onToggle: () => void
}) {
  const rows: Array<{ label: string; content: ReactNode }> = []
  if (moment.payoff !== '') rows.push({ label: 'Payoff', content: <p>{moment.payoff}</p> })
  rows.push({
    label: 'Category',
    content: (
      <p>
        {label(moment.category)}
        {moment.tags.length === 0 ? null : (
          <span className="text-muted-foreground"> · {moment.tags.join(', ')}</span>
        )}
      </p>
    ),
  })
  if (moment.contextDependencies.length > 0) {
    rows.push({ label: 'Depends on earlier context', content: <BulletList items={moment.contextDependencies} /> })
  }
  if (moment.visualOpportunities.length > 0) {
    rows.push({ label: 'Visual opportunities', content: <BulletList items={moment.visualOpportunities} /> })
  }

  return (
    <div role="group" aria-label="Why this moment" className="rounded-lg border bg-card">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-2 p-4 text-left text-small font-medium"
      >
        Why this moment
        <ChevronDown
          aria-hidden="true"
          strokeWidth={1.75}
          className={cn('size-4 text-muted-foreground transition-transform', open && 'rotate-180')}
        />
      </button>
      {open ? (
        <dl className="space-y-3 border-t px-4 pb-4 pt-3 text-small">
          {rows.map((row) => (
            <div key={row.label} className="space-y-0.5">
              <dt className="text-caption text-muted-foreground">{row.label}</dt>
              <dd>{row.content}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  )
}

function BulletList({ items }: { items: string[] }) {
  return (
    <ul className="list-disc space-y-0.5 pl-4">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  )
}

/** `question_answer` as `Question answer`. */
function label(category: string): string {
  const spaced = category.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}
