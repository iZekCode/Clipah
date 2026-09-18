import { act, fireEvent, renderHook, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { CommandPalette, useCommandPaletteShortcut } from '@/components/shell/command-palette'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { currentUser, workspace } from './support/fixtures'

const push = vi.hoisted(() => vi.fn())
vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
}))

beforeEach(() => {
  push.mockReset()
  window.sessionStorage.clear()
})

function palette(onOpenChange = vi.fn(), onNewProject = vi.fn()) {
  renderWithApi(
    <WorkspaceProvider>
      <CommandPalette open onOpenChange={onOpenChange} onNewProject={onNewProject} />
    </WorkspaceProvider>,
  )
  return { onOpenChange, onNewProject }
}

describe('the command palette', () => {
  test('jumps to any destination by name', async () => {
    const user = userEvent.setup()
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    })
    const { onOpenChange } = palette()

    await user.type(await screen.findByRole('combobox'), 'publi')
    await user.keyboard('{Enter}')

    expect(push).toHaveBeenCalledWith('/dashboard/publishing')
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  test('starts a new project', async () => {
    const user = userEvent.setup()
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
    })
    const { onNewProject } = palette()

    await user.click(await screen.findByRole('option', { name: 'New project' }))

    expect(onNewProject).toHaveBeenCalledTimes(1)
  })

  test('searches the active workspace and opens a result at its deep link', async () => {
    const user = userEvent.setup()
    const api = stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
      'GET /api/v1/search': {
        body: {
          results: [
            {
              id: 'result-1',
              type: 'clip',
              entityId: 'clip-1',
              projectId: 'project-1',
              projectName: 'Episode 12',
              title: 'The surprising opening',
              deepLink: '/dashboard/clips/clip-1',
              fragments: [],
              score: 1,
              speaker: null,
              startMs: null,
              endMs: null,
              language: 'en',
              topics: [],
              tags: [],
              exportState: 'not_exported',
              createdAt: '2026-02-01T00:00:00+00:00',
            },
          ],
          nextCursor: null,
        },
      },
    })
    palette()

    await user.type(await screen.findByRole('combobox'), 'surprising')
    await user.click(await screen.findByRole('option', { name: /the surprising opening/i }))

    const search = api.calls.find((call) => call.path === '/api/v1/search')
    expect(search?.params.get('q')).toBe('surprising')
    expect(search?.params.get('workspace_id')).toBe(workspace().id)
    expect(push).toHaveBeenCalledWith('/dashboard/clips/clip-1')
  })

  test('⌘K and Ctrl K open it from anywhere', () => {
    const onOpen = vi.fn()
    renderHook(() => useCommandPaletteShortcut(onOpen))

    act(() => {
      fireEvent.keyDown(window, { key: 'k', metaKey: true })
      fireEvent.keyDown(window, { key: 'K', ctrlKey: true })
    })

    expect(onOpen).toHaveBeenCalledTimes(2)
  })
})
