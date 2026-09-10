import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test } from 'vitest'

import { TeamSettings } from '@/features/team/TeamSettings'
import { InviteAcceptance } from '@/features/team/InviteAcceptance'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { capabilities, currentUser, workspace } from './support/fixtures'

const TEAM = workspace({ kind: 'team', role: 'owner', name: 'Review Team' })
const MEMBERS = `GET /api/v1/workspaces/${TEAM.id}/members`
const INVITES = `GET /api/v1/workspaces/${TEAM.id}/invites`

describe('team settings', () => {
  test('lists members and creates one explicit-role invite', async () => {
    const api = stubApi({
      'GET /api/v1/me': { body: currentUser({ capabilities: capabilities({ collaboration: true }) }) },
      'GET /api/v1/workspaces': { body: { workspaces: [TEAM] } },
      [MEMBERS]: {
        body: {
          members: [
            {
              userId: currentUser().id,
              displayName: 'Rin Creator',
              email: 'creator@example.com',
              role: 'owner',
              joinedAt: '2026-09-07T00:00:00+00:00',
            },
          ],
        },
      },
      [INVITES]: { body: { invites: [] } },
      [`POST /api/v1/workspaces/${TEAM.id}/invites`]: {
        status: 201,
        body: {
          id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          email: 'reviewer@example.test',
          role: 'reviewer',
          token: `${TEAM.id}.secret`,
          expiresAt: '2026-09-14T00:00:00+00:00',
        },
      },
    })
    renderWithApi(
      <WorkspaceProvider>
        <TeamSettings />
      </WorkspaceProvider>,
    )

    expect(await screen.findByText('Rin Creator')).toBeVisible()
    await userEvent.type(screen.getByLabelText(/invite email/i), 'reviewer@example.test')
    await userEvent.selectOptions(screen.getByLabelText(/invite role/i), 'reviewer')
    await userEvent.click(screen.getByRole('button', { name: /create invite/i }))

    await waitFor(() =>
      expect((screen.getByLabelText(/invite link/i) as HTMLInputElement).value).toContain(
        '.secret',
      ),
    )
    const call = api.calls.find((item) => item.method === 'POST')
    expect(call?.body).toEqual({ email: 'reviewer@example.test', role: 'reviewer' })
  })

  test('does not expose member controls when collaboration is disabled', async () => {
    stubApi({
      'GET /api/v1/me': { body: currentUser() },
      'GET /api/v1/workspaces': { body: { workspaces: [TEAM] } },
    })
    renderWithApi(
      <WorkspaceProvider>
        <TeamSettings />
      </WorkspaceProvider>,
    )

    expect(await screen.findByText(/not enabled/i)).toBeVisible()
    expect(screen.queryByLabelText(/invite email/i)).not.toBeInTheDocument()
  })
})

describe('invite acceptance', () => {
  test('binds the bearer invite to the independently signed-in user', async () => {
    const token = `${TEAM.id}.secret`
    const api = stubApi({
      'GET /api/v1/me': {
        body: currentUser({
          capabilities: capabilities({ collaboration: true }),
        }),
      },
      [`POST /api/v1/workspace-invites/${token}/accept`]: {
        body: {
          userId: currentUser().id,
          displayName: 'Rin Creator',
          email: currentUser().email,
          role: 'reviewer',
          joinedAt: '2026-09-07T00:00:00+00:00',
        },
      },
    })
    renderWithApi(<InviteAcceptance token={token} />)

    await userEvent.click(await screen.findByRole('button', { name: /accept invite/i }))

    await screen.findByText(/joined as reviewer/i)
    expect(api.calls.some((call) => call.method === 'POST')).toBe(true)
  })
})
