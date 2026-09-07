import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { ProjectDetail } from '@/features/projects/project-detail'
import { LibrarySearch } from '@/features/search/GlobalSearch'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import type { SearchResultResponse } from '@/lib/api/generated/model'

import { renderWithApi, stubApi, type Handler, type StubbedApi } from './support/api'
import { FakeEventSource } from './support/events'
import { currentUser, project, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const SEARCH = 'GET /api/v1/search'
const PROJECT_ID = '44444444-4444-4444-8444-444444444444'
const CANDIDATE_ID = '55555555-5555-4555-8555-555555555555'

const searchParams = { current: new URLSearchParams() }

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => searchParams.current,
}))

/** One transcript result, as the backend renders it. */
function transcriptResult(overrides: Partial<SearchResultResponse> = {}): SearchResultResponse {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    type: 'transcript',
    entityId: '22222222-2222-4222-8222-222222222222',
    projectId: PROJECT_ID,
    projectName: 'Rapat Produk',
    title: 'Formulir pendaftaran itu batasnya',
    fragments: [
      { text: 'Formulir ', highlighted: false },
      { text: 'pendaftaran', highlighted: true },
      { text: ' itu batasnya', highlighted: false },
    ],
    speaker: 'SPEAKER_00',
    topics: [],
    tags: [],
    language: 'id',
    startMs: 62_000,
    endMs: 94_000,
    exportState: 'not_exported',
    deepLink: `/dashboard/projects/${PROJECT_ID}?t=62000`,
    createdAt: '2026-02-01T00:00:00+00:00',
    score: 2.7,
    ...overrides,
  }
}

/** One clip result, which opens on the clip rather than on its Project. */
function clipResult(overrides: Partial<SearchResultResponse> = {}): SearchResultResponse {
  return transcriptResult({
    id: '33333333-3333-4333-8333-333333333333',
    type: 'clip',
    entityId: CANDIDATE_ID,
    title: 'Formulir itu batasnya',
    topics: ['produk'],
    tags: ['insight'],
    deepLink: `/dashboard/clips/${CANDIDATE_ID}`,
    exportState: 'exported',
    ...overrides,
  })
}

/** A stubbed backend signed in as one Workspace member with these results. */
function signedInApi(results: SearchResultResponse[], extra: Record<string, Handler> = {}): StubbedApi {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [SEARCH]: { body: { results, nextCursor: null } },
    ...extra,
  })
}

function renderSearch(props: Parameters<typeof LibrarySearch>[0] = {}) {
  return renderWithApi(
    <WorkspaceProvider>
      <LibrarySearch {...props} />
    </WorkspaceProvider>,
  )
}

/** Type one question into the search box and wait for the backend to be asked. */
async function ask(api: StubbedApi, question: string): Promise<void> {
  const box = await screen.findByRole('searchbox', { name: /search/i })
  await userEvent.clear(box)
  await userEvent.type(box, question)
  await waitFor(() => {
    expect(api.calls.some((call) => call.path === '/api/v1/search')).toBe(true)
  })
}

/** The first result card, which owns a nested topic list a plain role query would match. */
function firstResult(results: HTMLElement): HTMLElement {
  const card = results.querySelector(':scope > li')
  if (card === null) {
    throw new Error('the library rendered no result')
  }
  return card as HTMLElement
}

/** The parameters of the most recent search the component performed. */
function lastSearch(api: StubbedApi): URLSearchParams {
  const searches = api.calls.filter((call) => call.path === '/api/v1/search')
  const last = searches.at(-1)
  if (last === undefined) {
    throw new Error('the component performed no search')
  }
  return last.params
}

describe('the searchable content library', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    searchParams.current = new URLSearchParams()
  })

  test('nothing is asked of the backend until a member has asked something', async () => {
    const api = signedInApi([transcriptResult()])

    renderSearch()
    await screen.findByRole('searchbox', { name: /search/i })

    expect(api.calls.some((call) => call.path === '/api/v1/search')).toBe(false)
  })

  test('a question is answered with the moment it was found in', async () => {
    const api = signedInApi([transcriptResult()])
    renderSearch()

    await ask(api, 'pendaftaran')

    const results = await screen.findByRole('list', { name: /search results/i })
    const first = firstResult(results)
    expect(within(first).getByText('Rapat Produk')).toBeInTheDocument()
    expect(within(first).getByText(/SPEAKER_00/)).toBeInTheDocument()
    expect(within(first).getByText(/1:02/)).toBeInTheDocument()
    expect(within(first).getByRole('link')).toHaveAttribute(
      'href',
      `/dashboard/projects/${PROJECT_ID}?t=62000`,
    )
  })

  test('the words that matched are marked without the server sending markup', async () => {
    const api = signedInApi([transcriptResult()])
    renderSearch()

    await ask(api, 'pendaftaran')

    const results = await screen.findByRole('list', { name: /search results/i })
    const marked = within(results).getByText('pendaftaran')
    expect(marked.tagName).toBe('MARK')
    expect(within(results).getAllByText(/Formulir/).length).toBeGreaterThan(0)
  })

  test('a fragment containing a tag is shown as text and produces no element', async () => {
    const api = signedInApi([
      transcriptResult({
        title: '<img src=x onerror=alert(1)>',
        fragments: [{ text: '<img src=x onerror=alert(1)>', highlighted: false }],
      }),
    ])
    renderSearch()

    await ask(api, 'img')

    const results = await screen.findByRole('list', { name: /search results/i })
    expect(within(results).getAllByText('<img src=x onerror=alert(1)>').length).toBeGreaterThan(0)
    expect(results.querySelector('img')).toBeNull()
  })

  test('a clip result opens on the clip and says it has been exported', async () => {
    const api = signedInApi([clipResult()])
    renderSearch()

    await ask(api, 'formulir')

    const results = await screen.findByRole('list', { name: /search results/i })
    const first = firstResult(results)
    expect(within(first).getByRole('link')).toHaveAttribute(
      'href',
      `/dashboard/clips/${CANDIDATE_ID}`,
    )
    expect(within(first).getByText(/Exported/i)).toBeInTheDocument()
  })

  test('every filter the member sets is sent to the backend', async () => {
    const api = signedInApi([clipResult()])
    renderSearch()
    await ask(api, 'formulir')

    await userEvent.selectOptions(screen.getByLabelText(/content type/i), 'clip')
    await userEvent.selectOptions(screen.getByLabelText(/language/i), 'id')
    await userEvent.selectOptions(screen.getByLabelText(/export state/i), 'exported')
    await userEvent.type(screen.getByLabelText(/speaker/i), 'SPEAKER_00')
    await userEvent.type(screen.getByLabelText(/topic/i), 'produk')

    await waitFor(() => {
      const params = lastSearch(api)
      expect(params.getAll('type')).toEqual(['clip'])
      expect(params.get('language')).toBe('id')
      expect(params.get('exportState')).toBe('exported')
      expect(params.get('speaker')).toBe('SPEAKER_00')
      expect(params.get('topic')).toBe('produk')
    })
  })

  test('a date window is sent as the instant the day begins', async () => {
    const api = signedInApi([transcriptResult()])
    renderSearch()
    await ask(api, 'formulir')

    await userEvent.type(screen.getByLabelText(/from/i), '2026-01-15')

    await waitFor(() => {
      expect(lastSearch(api).get('createdAfter')).toBe('2026-01-15T00:00:00.000Z')
    })
  })

  test('a page opened for one kind of work searches only that kind', async () => {
    const api = signedInApi([clipResult()])
    renderSearch({ defaultTypes: ['clip'] })

    await ask(api, 'formulir')

    expect(lastSearch(api).getAll('type')).toEqual(['clip'])
  })

  test('a search can be narrowed to one of the member\'s own Projects', async () => {
    const api = signedInApi([transcriptResult()], {
      'GET /api/v1/projects': {
        body: { projects: [project({ id: PROJECT_ID, name: 'Rapat Produk' })], nextCursor: null },
      },
    })
    renderSearch()
    await ask(api, 'formulir')

    await userEvent.selectOptions(await screen.findByLabelText(/^project$/i), PROJECT_ID)

    await waitFor(() => {
      expect(lastSearch(api).get('projectId')).toBe(PROJECT_ID)
    })
  })

  test('a search is scoped to the Workspace being viewed', async () => {
    const api = signedInApi([transcriptResult()])
    renderSearch()

    await ask(api, 'formulir')

    expect(lastSearch(api).get('workspace_id')).toBe(workspace().id)
  })

  test('a question with no answers says so instead of showing an empty list', async () => {
    const api = signedInApi([])
    renderSearch()

    await ask(api, 'tidakada')

    expect(await screen.findByText(/nothing in this workspace matches/i)).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: /search results/i })).toBeNull()
  })

  test('more results are fetched only when the member asks for them', async () => {
    const api = signedInApi([transcriptResult()], {
      [SEARCH]: (request) =>
        request.params.get('cursor') === null
          ? { body: { results: [transcriptResult()], nextCursor: 'next-page' } }
          : { body: { results: [clipResult()], nextCursor: null } },
    })
    renderSearch()
    await ask(api, 'formulir')

    const more = await screen.findByRole('button', { name: /more results/i })
    await userEvent.click(more)

    await waitFor(() => {
      expect(lastSearch(api).get('cursor')).toBe('next-page')
    })
    const results = await screen.findByRole('list', { name: /search results/i })
    expect(results.querySelectorAll(':scope > li')).toHaveLength(2)
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /more results/i })).toBeNull()
    })
  })

  test('a refusal is reported with the identifier support will ask for', async () => {
    const api = signedInApi([], {
      [SEARCH]: {
        status: 500,
        body: {
          error: {
            code: 'INTERNAL_ERROR',
            message: 'Something went wrong. Please try again.',
            requestId: 'request-9',
          },
        },
      },
    })
    renderSearch()

    await ask(api, 'formulir')

    expect(await screen.findByRole('alert')).toHaveTextContent('request-9')
  })
})


describe('opening a transcript result at its timecode', () => {
  const PROXY = `GET /api/v1/projects/${PROJECT_ID}/proxy`
  const PROJECT = `GET /api/v1/projects/${PROJECT_ID}`

  /** A stubbed backend holding one ready Project and its signed proxy. */
  function projectApi(extra: Record<string, Handler> = {}): StubbedApi {
    return stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECT]: { body: project({ id: PROJECT_ID }) },
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

  beforeEach(() => {
    vi.unstubAllGlobals()
    searchParams.current = new URLSearchParams()
    // The Project screen follows the Workspace job stream, which jsdom has no client for.
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  test('a Project opened at a timecode plays the proxy from that moment', async () => {
    searchParams.current = new URLSearchParams('t=62000')
    projectApi()

    renderWithApi(
      <WorkspaceProvider>
        <ProjectDetail projectId={PROJECT_ID} />
      </WorkspaceProvider>,
    )

    const player = await screen.findByTestId('transcript-moment-video')
    expect(player).toHaveAttribute('src', 'https://objects.test/proxy.mp4?signature=secret')
    fireEvent.loadedMetadata(player)
    expect((player as HTMLVideoElement).currentTime).toBe(62)
  })

  test('a Project opened without a timecode asks for no capability at all', async () => {
    const api = projectApi()

    renderWithApi(
      <WorkspaceProvider>
        <ProjectDetail projectId={PROJECT_ID} />
      </WorkspaceProvider>,
    )
    await screen.findByRole('heading', { level: 1 })

    expect(screen.queryByTestId('transcript-moment-video')).toBeNull()
    expect(api.calls.some((call) => call.path.endsWith('/proxy'))).toBe(false)
  })
})
