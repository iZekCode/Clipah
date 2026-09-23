/**
 * The guided creator journey: browse clips, export exactly what was saved, and publish
 * from a finished export — plus the library and Settings surfaces that support it.
 *
 * Each behaviour here is one a creator relies on without seeing it: that a filter asks
 * the backend rather than hiding rows, that an export names the Revision the save
 * produced, that switching editing tools never throws away a draft, and that a
 * publication always starts from one exact rendered file.
 */
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { AssetBrowser } from '@/features/assets/AssetBrowser'
import { Autosave, memoryDraftStore } from '@/features/editor/autosave'
import { EditorScreen } from '@/features/editor/EditorScreen'
import { ExportDialog } from '@/features/editor/ExportDialog'
import { ExportList } from '@/features/exports/export-list'
import { ProjectDetail } from '@/features/projects/project-detail'
import { NewPublication } from '@/features/publishing/NewPublication'
import { GeneralSettings } from '@/features/settings/GeneralSettings'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import { ApiError } from '@/lib/api/client'
import type { CompositionV1, ExportResponse } from '@/lib/api/generated/model'

import { errorBody, renderWithApi, stubApi } from './support/api'
import { expectAccessible } from './support/axe'
import { FakeEventSource } from './support/events'
import {
  capabilities,
  composition,
  currentUser,
  edit,
  project,
  workspace,
} from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const EXPORTS = 'GET /api/v1/exports'
const PROJECTS = 'GET /api/v1/projects'
const EDIT_ID = edit().id
const PROJECT_ID = project().id
const RENDER_ID = '99999999-0000-4000-8000-000000000001'

const push = vi.hoisted(() => vi.fn())

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

function exported(overrides: Partial<ExportResponse> = {}): ExportResponse {
  return {
    id: '88888888-0000-4000-8000-000000000001',
    jobId: '88888888-0000-4000-8000-000000000002',
    status: 'ready',
    errorCode: null,
    preset: '1080x1920',
    projectId: PROJECT_ID,
    projectName: 'Episode 12',
    candidateId: edit().candidateId,
    editId: EDIT_ID,
    revisionId: '88888888-0000-4000-8000-000000000003',
    revision: 2,
    renderId: RENDER_ID,
    durationMs: 30_000,
    sizeBytes: 4_096_000,
    createdAt: '2026-02-01T00:00:00+00:00',
    completedAt: '2026-02-01T00:01:00+00:00',
    ...overrides,
  }
}

beforeEach(() => {
  push.mockReset()
  window.sessionStorage.clear()
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

describe('exporting from the editor', () => {
  function renderDialog(saveNow: () => Promise<boolean>, revision = 2) {
    renderWithApi(
      <WorkspaceProvider>
        <ExportDialog
          open
          onOpenChange={() => undefined}
          editId={EDIT_ID}
          projectId={PROJECT_ID}
          sourceRange={{ inMs: 1_000, outMs: 31_000 }}
          workspaceId={workspace().id}
          defaultPreset="1080x1920"
          mayExport
          saveNow={saveNow}
          currentRevision={() => revision}
        />
      </WorkspaceProvider>,
    )
  }

  test('saves first, then asks for a render bound to the Revision that save produced', async () => {
    const user = userEvent.setup()
    const order: string[] = []
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [EXPORTS]: { body: { exports: [], nextCursor: null } },
      [`POST /api/v1/edits/${EDIT_ID}/renders`]: () => {
        order.push('render')
        return { status: 202, body: { status: 'rendering', jobId: 'job-1' } }
      },
    })
    renderDialog(async () => {
      order.push('save')
      return true
    })

    await user.click(await screen.findByRole('radio', { name: /square 1:1/i }))
    await user.click(screen.getByRole('button', { name: /^export$/i }))

    await waitFor(() => expect(order).toEqual(['save', 'render']))
    const request = api.calls.find((call) => call.method === 'POST')
    expect(request?.body).toEqual({ preset: '1080x1080', expectedRevision: 2 })
    expect(request?.headers.get('Idempotency-Key')).toBe(`render:${EDIT_ID}:r2:1080x1080`)
    // Every format is shown as the clip itself, cropped to that shape.
    expect(screen.getAllByTestId('poster')).toHaveLength(4)
  })

  test('exports nothing when the latest changes could not be saved', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [EXPORTS]: { body: { exports: [], nextCursor: null } },
    })
    renderDialog(async () => false)

    await user.click(await screen.findByRole('button', { name: /^export$/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/not saved yet/i)
    expect(api.calls.some((call) => call.method === 'POST')).toBe(false)
  })

  test('explains a render refused because the clip moved past the saved Revision', async () => {
    const user = userEvent.setup()
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [EXPORTS]: { body: { exports: [], nextCursor: null } },
      [`POST /api/v1/edits/${EDIT_ID}/renders`]: {
        status: 409,
        body: errorBody(409, 'EDIT_REVISION_CONFLICT'),
        headers: { 'X-Clipah-Current-Revision': '3' },
      },
    })
    renderDialog(async () => true)

    await user.click(await screen.findByRole('button', { name: /^export$/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/changed after it was saved/i)
  })
})

describe('the export list', () => {
  test('shows each state and offers download and publishing only for a finished file', async () => {
    const user = userEvent.setup()
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    const api = stubApi({
      [ME]: { body: currentUser({ capabilities: capabilities({ socialPublishing: true }) }) },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [EXPORTS]: {
        body: {
          exports: [
            exported(),
            exported({ id: 'e2', status: 'rendering', renderId: null, preset: '1920x1080' }),
            exported({ id: 'e3', status: 'failed', renderId: null, errorCode: 'RENDER_FAILED', preset: '1080x1080' }),
          ],
          nextCursor: null,
        },
      },
      [`GET /api/v1/renders/${RENDER_ID}/download-url`]: {
        body: { url: 'https://storage.test/clip.mp4?signature=short', expiresAt: '2026-02-01T00:05:00+00:00' },
      },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ExportList editId={EDIT_ID} />
      </WorkspaceProvider>,
    )

    const list = await screen.findByRole('list', { name: 'Exports' })
    expect(within(list).getByText('Ready')).toBeInTheDocument()
    expect(within(list).getByText('Rendering')).toBeInTheDocument()
    expect(within(list).getByText(/could not be finished \(RENDER_FAILED\)/)).toBeInTheDocument()
    expect(within(list).getAllByRole('button', { name: /download/i })).toHaveLength(1)
    const publish = await within(list).findByRole('link', { name: /publish/i })
    expect(publish.getAttribute('href')).toContain(`renderArtifactId=${RENDER_ID}`)
    expect(publish.getAttribute('href')).toContain('revision=2')

    await user.click(within(list).getByRole('button', { name: /download/i }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://storage.test/clip.mp4?signature=short'))
    expect(api.calls.some((call) => call.path.endsWith('/download-url'))).toBe(true)
    vi.unstubAllGlobals()
  })
})

describe('starting a publication', () => {
  test('begins by choosing one finished export', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser({ capabilities: capabilities({ socialPublishing: true, youtubePublishing: true }) }) },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [EXPORTS]: { body: { exports: [exported()], nextCursor: null } },
      [`GET /api/v1/workspaces/${workspace().id}/social-accounts`]: { body: { socialAccounts: [] } },
      [`GET /api/v1/renders/${RENDER_ID}/download-url`]: {
        body: { url: 'https://storage.test/clip.mp4', expiresAt: '2026-02-01T00:05:00+00:00' },
      },
    })

    renderWithApi(
      <WorkspaceProvider>
        <NewPublication preselected={null} />
      </WorkspaceProvider>,
    )

    await user.click(await screen.findByRole('button', { name: /use this export/i }))

    expect(await screen.findByRole('heading', { name: /publish this clip/i })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /export to publish/i })).toHaveTextContent('Revision 2')
    const read = api.calls.find((call) => call.path === '/api/v1/exports')
    expect(read?.params.get('state')).toBe('ready')
  })

  test('arriving from an export preselects it and skips the choice', async () => {
    stubApi({
      [ME]: { body: currentUser({ capabilities: capabilities({ socialPublishing: true, youtubePublishing: true }) }) },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [`GET /api/v1/workspaces/${workspace().id}/social-accounts`]: { body: { socialAccounts: [] } },
      [`GET /api/v1/renders/${RENDER_ID}/download-url`]: {
        body: { url: 'https://storage.test/clip.mp4', expiresAt: '2026-02-01T00:05:00+00:00' },
      },
    })

    renderWithApi(
      <WorkspaceProvider>
        <NewPublication preselected={{ editId: EDIT_ID, revision: 4, renderArtifactId: RENDER_ID, durationMs: 30_000 }} />
      </WorkspaceProvider>,
    )

    expect(await screen.findByRole('heading', { name: /publish this clip/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /use this export/i })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: /export to publish/i })).toHaveTextContent('Revision 4')
  })
})

describe('one project', () => {
  test('names the next step from its durable state and opens its exports on request', async () => {
    const user = userEvent.setup()
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [`GET /api/v1/projects/${PROJECT_ID}`]: { body: project({ status: 'created' }) },
      [EXPORTS]: { body: { exports: [exported()], nextCursor: null } },
    })

    renderWithApi(
      <WorkspaceProvider>
        <ProjectDetail projectId={PROJECT_ID} />
      </WorkspaceProvider>,
    )

    expect(await screen.findByRole('button', { name: 'Add media' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /upload a video/i })).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Exports' }))

    expect(await screen.findByRole('list', { name: 'Exports' })).toHaveTextContent('Vertical 9:16')
  })
})

describe('the asset library', () => {
  test('lists member media with its origin and signs a preview only when asked', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [PROJECTS]: { body: { projects: [project()], nextCursor: null } },
      'GET /api/v1/assets': {
        body: {
          assets: [
            {
              id: 'asset-1',
              projectId: PROJECT_ID,
              projectName: 'Episode 12',
              kind: 'broll',
              sourceType: 'stock',
              contentType: 'video/mp4',
              sizeBytes: 2_048_000,
              durationMs: 8_000,
              width: 1920,
              height: 1080,
              createdAt: '2026-02-01T00:00:00+00:00',
              provenance: {
                provider: 'pexels',
                author: 'A. Photographer',
                licenseName: 'Pexels License',
                licenseUrl: 'https://www.pexels.com/license/',
                sourceUrl: 'https://www.pexels.com/video/1/',
                attributionText: 'Video by A. Photographer',
                generated: false,
              },
            },
          ],
          nextCursor: null,
        },
      },
      'GET /api/v1/assets/asset-1/preview-url': {
        body: { url: 'https://storage.test/broll.mp4', expiresAt: '2026-02-01T00:05:00+00:00', contentType: 'video/mp4' },
      },
    })

    const { container } = renderWithApi(
      <WorkspaceProvider>
        <AssetBrowser />
      </WorkspaceProvider>,
    )

    const list = await screen.findByRole('list', { name: 'Assets' })
    await expectAccessible(container)
    expect(list).toHaveTextContent('Video by A. Photographer')
    expect(within(list).getByRole('link', { name: 'Pexels License' })).toHaveAttribute(
      'href',
      'https://www.pexels.com/license/',
    )
    expect(api.calls.some((call) => call.path.endsWith('/preview-url'))).toBe(false)

    await user.selectOptions(screen.getByRole('combobox', { name: 'Type' }), 'broll')
    await waitFor(() => {
      const last = api.calls.filter((call) => call.path === '/api/v1/assets').at(-1)
      expect(last?.params.get('kind')).toBe('broll')
    })

    await user.click(await screen.findByRole('button', { name: /preview b-roll from episode 12/i }))
    await waitFor(() => expect(api.calls.some((call) => call.path.endsWith('/preview-url'))).toBe(true))
    const sheet = screen.getByRole('dialog', { name: /b-roll from episode 12/i })
    expect(within(sheet).getByText('video/mp4')).toBeInTheDocument()
  })
})

describe('settings', () => {
  test('shows monthly usage and signs other devices out on request', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      'GET /api/v1/dashboard/summary': {
        body: {
          workspace: { id: workspace().id, name: 'Rin Creator', role: 'owner' },
          projects: { activeCount: 1, recent: [] },
          jobs: { active: [] },
          usage: [{ resource: 'analyses', consumed: 4, limit: 30 }],
          topCandidates: [],
        },
      },
      'GET /api/v1/me/sessions': {
        body: {
          sessions: [
            { id: 's1', createdAt: '2026-02-01T00:00:00+00:00', lastSeenAt: '2026-02-02T00:00:00+00:00', userAgent: 'Safari on macOS', current: true },
            { id: 's2', createdAt: '2026-01-01T00:00:00+00:00', lastSeenAt: '2026-01-02T00:00:00+00:00', userAgent: 'Chrome on Android', current: false },
          ],
        },
      },
      'DELETE /api/v1/me/sessions': { body: { revokedCount: 1 } },
    })

    const { container } = renderWithApi(
      <WorkspaceProvider>
        <GeneralSettings />
      </WorkspaceProvider>,
    )

    expect(await screen.findByRole('group', { name: 'Analyses' })).toHaveTextContent('4 of 30')
    await screen.findByRole('list', { name: 'Sessions' })
    await expectAccessible(container)
    expect(screen.getByRole('meter', { name: 'Analyses' })).toHaveAttribute('aria-valuenow', '4')
    const sessions = await screen.findByRole('list', { name: 'Sessions' })
    expect(within(sessions).getByText('This device')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /sign out other devices/i }))

    await waitFor(() =>
      expect(api.calls.some((call) => call.method === 'DELETE' && call.path === '/api/v1/me/sessions')).toBe(true),
    )
  })

  test('lets an owner rename the workspace', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [`PATCH /api/v1/workspaces/${workspace().id}`]: { body: workspace({ name: 'Studio Rin' }) },
    })

    renderWithApi(
      <WorkspaceProvider>
        <GeneralSettings />
      </WorkspaceProvider>,
    )

    const field = await screen.findByRole('textbox', { name: /workspace name/i })
    await user.clear(field)
    await user.type(field, 'Studio Rin')
    await user.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => {
      const patch = api.calls.find((call) => call.method === 'PATCH')
      expect(patch?.body).toEqual({ name: 'Studio Rin' })
    })
  })
})

describe('the editor studio', () => {
  function stubEditor(document: CompositionV1 = composition()) {
    return stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace()] } },
      [`GET /api/v1/edits/${EDIT_ID}`]: { body: edit({ composition: document }) },
      [`GET /api/v1/projects/${PROJECT_ID}`]: { body: project({ status: 'ready' }) },
      [`GET /api/v1/projects/${PROJECT_ID}/proxy`]: {
        body: {
          url: 'https://storage.test/proxy.mp4',
          expiresAt: '2026-02-01T00:05:00+00:00',
          contentType: 'video/mp4',
          durationMs: 60_000,
          width: 1920,
          height: 1080,
        },
      },
      [EXPORTS]: { body: { exports: [], nextCursor: null } },
    })
  }

  test('switching tools keeps a half-written draft where the member left it', async () => {
    const user = userEvent.setup()
    stubEditor()
    renderWithApi(<EditorScreen editId={EDIT_ID} />)
    await screen.findByRole('region', { name: /timeline/i })

    await user.click(screen.getByRole('tab', { name: 'Text' }))
    await user.type(screen.getByRole('textbox', { name: /new text/i }), 'Half a thought')
    await user.click(screen.getByRole('tab', { name: 'Captions' }))
    expect(screen.getByRole('tab', { name: 'Captions' })).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByRole('tab', { name: 'Text' }))

    expect(screen.getByRole('textbox', { name: /new text/i })).toHaveValue('Half a thought')
  })

  test('keeps the way back to the project and Export in the header', async () => {
    const user = userEvent.setup()
    stubEditor()
    renderWithApi(<EditorScreen editId={EDIT_ID} />)
    await screen.findByRole('region', { name: /timeline/i })

    expect(await screen.findByRole('link', { name: 'Episode 12' })).toHaveAttribute(
      'href',
      `/dashboard/projects/${PROJECT_ID}`,
    )
    await user.click(screen.getByRole('button', { name: /^export$/i }))

    expect(await screen.findByRole('dialog', { name: /export clip/i })).toBeInTheDocument()
    await act(async () => undefined)
  })
})

describe('saving before an export', () => {
  test('waits for the pending change to reach the backend and reports the new Revision', async () => {
    const pending: Array<(value: { currentRevision: number; composition: CompositionV1 }) => void> = []
    const autosave = new Autosave({
      editId: EDIT_ID,
      revision: 1,
      store: memoryDraftStore(),
      isOnline: () => true,
      save: () =>
        new Promise((resolve) => {
          pending.push(resolve)
        }),
      onStatus: () => undefined,
      onSaved: () => undefined,
      onConflict: () => undefined,
    })

    autosave.queue(composition())
    const settled = autosave.saveNow(1, 50)
    await waitFor(() => expect(pending).toHaveLength(1))
    pending[0]?.({ currentRevision: 2, composition: composition() })

    await expect(settled).resolves.toBe(true)
    expect(autosave.expectedRevision).toBe(2)
    autosave.dispose()
  })

  test('answers no when the save conflicts, so nothing stale is exported', async () => {
    const autosave = new Autosave({
      editId: EDIT_ID,
      revision: 1,
      store: memoryDraftStore(),
      isOnline: () => true,
      save: () => Promise.reject(new ApiError({ status: 409, code: 'EDIT_REVISION_CONFLICT', message: 'changed', requestId: 'r', currentRevision: 3 })),
      onStatus: () => undefined,
      onSaved: () => undefined,
      onConflict: () => undefined,
    })

    autosave.queue(composition())

    await expect(autosave.saveNow(1, 50)).resolves.toBe(false)
    autosave.dispose()
  })
})
