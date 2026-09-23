import { screen, waitFor, within } from '@testing-library/react'
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

  test('shows the hook and sends the reviewer to review mode for the reasons behind it', async () => {
    signedInApi([candidate()])

    renderClips()

    const clip = await firstClip()
    const review = `/dashboard/projects/${PROJECT_ID}/review?moment=${candidate().id}`
    expect(within(clip).getByRole('heading', { level: 3 })).toHaveTextContent(
      'The surprising opening',
    )
    expect(within(clip).getByRole('link', { name: 'The surprising opening' })).toHaveAttribute(
      'href',
      review,
    )
    expect(within(clip).getByRole('link', { name: 'View clip' })).toHaveAttribute('href', review)
    expect(within(clip).getByRole('button', { name: 'Edit clip' })).toBeInTheDocument()
    // The card carries no stage: where a clip has got to is not what a reviewer decides on.
    expect(clip).not.toHaveTextContent(/suggested|in editing|exported/i)
  })


  test('flags a context warning on the card itself, with every warning behind the flag', async () => {
    signedInApi([
      candidate({ contextWarnings: ['Needs a source overlay.', 'Names an unverified claim.'] }),
    ])

    renderClips()

    const clip = await firstClip()
    const flag = within(clip).getByText('Check context')
    expect(flag.closest('[title]')).toHaveAttribute(
      'title',
      'Needs a source overlay. Names an unverified claim.',
    )
  })

  test('shows the score beside the rank, and says it to assistive technology too', async () => {
    signedInApi([candidate({ score: 0.91 })])

    renderClips()

    const clip = await firstClip()
    expect(within(clip).getByTestId('poster-score')).toHaveTextContent('91')
    expect(within(clip).getByText('Score 91')).toHaveClass('sr-only')
  })

  test('lays the sharp poster over the storyboard tile once it has been drawn', async () => {
    const first = candidate()
    const second = candidate({ id: 'aaaaaaa1-0000-4000-8000-000000000002', rank: 2 })
    signedInApi([first, second], {
      [`GET /api/v1/projects/${PROJECT_ID}/posters`]: {
        body: {
          posters: [
            {
              candidateId: first.id,
              width: 404,
              height: 720,
              url: 'https://objects.test/poster.jpg?signature=short',
            },
          ],
          expiresAt: '2026-02-01T00:05:00+00:00',
        },
      },
    })

    renderClips()

    const list = await screen.findByRole('list', { name: /ranked clips/i })
    const [drawn, waiting] = Array.from(list.querySelectorAll(':scope > li'))
    await waitFor(() => {
      expect(drawn!.querySelector('img[src^="https://objects.test/poster.jpg"]')).not.toBeNull()
    })
    expect(waiting!.querySelector('img[src^="https://objects.test/poster.jpg"]')).toBeNull()
  })

  test('shows the length a reviewer is deciding on', async () => {
    signedInApi([candidate({ durationMs: 90_000 })])

    renderClips()

    expect(await firstClip()).toHaveTextContent('1:30')
  })

  test('draws each moment as a poster and lets the page select it', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    signedInApi([candidate()])
    renderWithApi(
      <WorkspaceProvider>
        <ClipList projectId={PROJECT_ID} onSelect={onSelect} />
      </WorkspaceProvider>,
    )

    const clip = await firstClip()
    expect(within(clip).getByTestId('poster')).toBeInTheDocument()
    await user.click(within(clip).getByRole('button', { name: 'Show in source' }))

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: candidate().id }))
  })

  test('a clip without warnings carries no flag', async () => {
    signedInApi([candidate({ contextWarnings: [] })])
    renderClips()

    expect(within(await firstClip()).queryByText('Check context')).toBeNull()
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
        contextWarnings: ['<script>alert("no")</script>'],
      }),
    ])

    renderClips()

    const clip = await firstClip()
    expect(within(clip).getByRole('heading', { level: 3 })).toHaveTextContent(
      '<img src=x onerror="alert(1)">',
    )
    expect(within(clip).getByText('Check context').closest('[title]')).toHaveAttribute(
      'title',
      '<script>alert("no")</script>',
    )
    expect(clip.querySelector('script')).toBeNull()
    expect(clip.querySelector('img[src="x"]')).toBeNull()
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
