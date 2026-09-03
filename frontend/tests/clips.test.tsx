import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { ClipList } from '@/features/clips/ClipList'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi, type Handler, type StubbedApi } from './support/api'
import { candidate, currentUser, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const PROJECT_ID = '44444444-4444-4444-8444-444444444444'
const CANDIDATES = `GET /api/v1/projects/${PROJECT_ID}/candidates`
const PROXY = `GET /api/v1/projects/${PROJECT_ID}/proxy`

vi.mock('next/navigation', () => ({
  usePathname: () => `/dashboard/projects/${PROJECT_ID}`,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}))

/** A stubbed backend that answers as a Workspace with these ranked clips. */
function signedInApi(
  candidates: ReturnType<typeof candidate>[],
  extra: Record<string, Handler> = {},
): StubbedApi {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [CANDIDATES]: { body: { candidates, nextCursor: null } },
    [PROXY]: {
      body: {
        url: 'https://objects.test/proxy.mp4?signature=secret',
        expiresAt: '2026-02-01T00:05:00+00:00',
        contentType: 'video/mp4',
        durationMs: 600_000,
        width: 1280,
        height: 720,
      },
    },
    ...extra,
  })
}

/** Render the review list inside the Workspace the member is viewing. */
function renderClips() {
  return renderWithApi(
    <WorkspaceProvider>
      <ClipList projectId={PROJECT_ID} />
    </WorkspaceProvider>,
  )
}

/** The first clip card, which owns nested lists a plain role query would also match. */
async function firstClip(): Promise<HTMLElement> {
  const list = await screen.findByRole('list', { name: /ranked clips/i })
  const card = list.querySelector(':scope > li')
  if (card === null) {
    throw new Error('the list rendered no clip')
  }
  return card as HTMLElement
}

/** The rendered clips, in the order the list put them on the page. */
async function listedHooks(): Promise<string[]> {
  const list = await screen.findByRole('list', { name: /ranked clips/i })
  return within(list)
    .getAllByRole('heading', { level: 3 })
    .map((heading) => heading.textContent ?? '')
}

/** Three ranked clips that differ in the ways the filters and sorts care about. */
function threeClips() {
  return [
    candidate({
      id: 'aaaaaaa1-0000-4000-8000-000000000001',
      rank: 1,
      score: 0.91,
      hook: 'The best moment',
      category: 'insight',
      durationMs: 30_000,
      endMs: 31_000,
    }),
    candidate({
      id: 'aaaaaaa1-0000-4000-8000-000000000002',
      rank: 2,
      score: 0.72,
      hook: 'The long story',
      category: 'story',
      durationMs: 90_000,
      endMs: 91_000,
    }),
    candidate({
      id: 'aaaaaaa1-0000-4000-8000-000000000003',
      rank: 3,
      score: 0.81,
      hook: 'The quick answer',
      category: 'question_answer',
      durationMs: 15_000,
      endMs: 16_000,
    }),
  ]
}

beforeEach(() => {
  window.sessionStorage.clear()
  vi.stubGlobal('EventSource', class {})
})

describe('the ranked clip list', () => {
  test('keeps the ranking the backend decided rather than an order of its own', async () => {
    signedInApi(threeClips())

    renderClips()

    expect(await listedHooks()).toEqual(['The best moment', 'The long story', 'The quick answer'])
  })

  test('reads every exposed page before it offers to sort or filter anything', async () => {
    const first = candidate({ id: 'page-one', rank: 1, hook: 'First page clip' })
    const second = candidate({ id: 'page-two', rank: 2, hook: 'Second page clip' })
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [CANDIDATES]: (request) =>
        request.params.get('cursor') === null
          ? { body: { candidates: [first], nextCursor: 'next-page' } }
          : { body: { candidates: [second], nextCursor: null } },
    })

    renderClips()

    await waitFor(async () =>
      expect(await listedHooks()).toEqual(['First page clip', 'Second page clip']),
    )
    expect(api.calls.filter((call) => call.path.endsWith('/candidates'))).toHaveLength(2)
  })

  test('shows the score, the hook, the payoff, and why the clip was chosen', async () => {
    signedInApi([candidate()])

    renderClips()

    const clip = await firstClip()
    expect(within(clip).getByRole('heading', { level: 3 })).toHaveTextContent(
      'The surprising opening',
    )
    expect(clip).toHaveTextContent('The useful resolution')
    expect(clip).toHaveTextContent('A complete and useful moment')
    expect(clip).toHaveTextContent('91')
  })

  test('explains the score through every dimension the analysis reported', async () => {
    signedInApi([candidate()])

    renderClips()

    const clip = await firstClip()
    for (const dimension of [
      /hook/i,
      /payoff/i,
      /narrative completeness/i,
      /context safety/i,
      /platform fit/i,
      /transcript confidence/i,
      /visual opportunity/i,
    ]) {
      expect(within(clip).getByText(dimension)).toBeInTheDocument()
    }
  })

  test('puts the context warnings where a reviewer cannot miss them', async () => {
    signedInApi([
      candidate({ contextWarnings: ['Needs a source overlay.', 'Names an unverified claim.'] }),
    ])

    renderClips()

    const warnings = await screen.findByRole('group', { name: /context warnings/i })
    expect(warnings).toHaveTextContent('Needs a source overlay.')
    expect(warnings).toHaveTextContent('Names an unverified claim.')
  })

  test('shows the category, the tags, and the length a reviewer is deciding on', async () => {
    signedInApi([candidate({ category: 'question_answer', durationMs: 90_000 })])

    renderClips()

    const clip = await firstClip()
    expect(clip).toHaveTextContent(/question and answer/i)
    expect(clip).toHaveTextContent('creator')
    expect(clip).toHaveTextContent('1:30')
  })

  test('shows only the category the reviewer asked for', async () => {
    const user = userEvent.setup()
    signedInApi(threeClips())

    renderClips()
    await listedHooks()
    await user.selectOptions(screen.getByRole('combobox', { name: /category/i }), 'story')

    expect(await listedHooks()).toEqual(['The long story'])
  })

  test('drops clips longer than the length the reviewer will publish', async () => {
    const user = userEvent.setup()
    signedInApi(threeClips())

    renderClips()
    await listedHooks()
    await user.selectOptions(screen.getByRole('combobox', { name: /length/i }), '30000')

    expect(await listedHooks()).toEqual(['The best moment', 'The quick answer'])
  })

  test('sorts by score and by length when asked, and says which order is showing', async () => {
    const user = userEvent.setup()
    signedInApi(threeClips())

    renderClips()
    await listedHooks()

    await user.selectOptions(screen.getByRole('combobox', { name: /sort/i }), 'score')
    expect(await listedHooks()).toEqual(['The best moment', 'The quick answer', 'The long story'])

    await user.selectOptions(screen.getByRole('combobox', { name: /sort/i }), 'longest')
    expect(await listedHooks()).toEqual(['The long story', 'The best moment', 'The quick answer'])

    await user.selectOptions(screen.getByRole('combobox', { name: /sort/i }), 'rank')
    expect(await listedHooks()).toEqual(['The best moment', 'The long story', 'The quick answer'])
  })

  test('renders provider text as text, whatever it happens to contain', async () => {
    signedInApi([
      candidate({
        hook: '<img src=x onerror="alert(1)">',
        reason: '<script>alert("no")</script>',
      }),
    ])

    renderClips()

    const clip = await firstClip()
    expect(within(clip).getByRole('heading', { level: 3 })).toHaveTextContent(
      '<img src=x onerror="alert(1)">',
    )
    expect(clip).toHaveTextContent('<script>alert("no")</script>')
    expect(clip.querySelector('script')).toBeNull()
    expect(clip.querySelector('img')).toBeNull()
  })

  test('says plainly when the analysis produced nothing worth reviewing', async () => {
    signedInApi([])

    renderClips()

    expect(await screen.findByText(/no clips/i)).toBeInTheDocument()
  })

  test('says there is nothing to review yet rather than alarming about a missing Project', async () => {
    signedInApi([], { [CANDIDATES]: { status: 404 } })

    renderClips()

    expect(await screen.findByText(/no clips to review yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  test('still surfaces a real failure the reviewer can quote to support', async () => {
    signedInApi([], { [CANDIDATES]: { status: 500 } })

    renderClips()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/went wrong/i)
    expect(alert).toHaveTextContent('request-1234')
  })
})

describe('clip preview', () => {
  test('plays the candidate range against the project proxy and stops at its end', async () => {
    const user = userEvent.setup()
    const played = vi.fn()
    const paused = vi.fn()
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(async () => {
      played()
    })
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {
      paused()
    })
    signedInApi([candidate({ startMs: 12_000, endMs: 42_000, durationMs: 30_000 })])

    renderClips()
    await user.click(await screen.findByRole('button', { name: /preview/i }))

    const video = await screen.findByTestId('clip-preview-video')
    expect(video).toHaveAttribute('src', 'https://objects.test/proxy.mp4?signature=secret')

    fireEvent.loadedMetadata(video)
    await waitFor(() => expect(played).toHaveBeenCalled())
    expect((video as HTMLVideoElement).currentTime).toBe(12)
    ;(video as HTMLVideoElement).currentTime = 42
    fireEvent.timeUpdate(video)

    await waitFor(() => expect(paused).toHaveBeenCalled())
    expect((video as HTMLVideoElement).currentTime).toBe(12)
  })

  test('is operable from the keyboard alone', async () => {
    const user = userEvent.setup()
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    signedInApi([candidate()])

    renderClips()
    const preview = await screen.findByRole('button', { name: /preview/i })
    preview.focus()
    await user.keyboard('{Enter}')

    expect(await screen.findByTestId('clip-preview-video')).toBeInTheDocument()
  })

  test('says the preview is not ready when the project has produced no proxy', async () => {
    const user = userEvent.setup()
    signedInApi([candidate()], { [PROXY]: { status: 404 } })

    renderClips()
    await user.click(await screen.findByRole('button', { name: /preview/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/not found/i)
    expect(screen.queryByTestId('clip-preview-video')).not.toBeInTheDocument()
  })

  test('asks for a fresh capability every time a preview is opened', async () => {
    const user = userEvent.setup()
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const api = signedInApi([candidate()])

    renderClips()
    const preview = await screen.findByRole('button', { name: /preview/i })
    await user.click(preview)
    await screen.findByTestId('clip-preview-video')
    await user.click(screen.getByRole('button', { name: /preview/i }))
    await user.click(screen.getByRole('button', { name: /preview/i }))
    await screen.findByTestId('clip-preview-video')

    await waitFor(() =>
      expect(api.calls.filter((call) => call.path.endsWith('/proxy')).length).toBeGreaterThan(1),
    )
  })
})
