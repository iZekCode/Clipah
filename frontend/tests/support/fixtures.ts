import type { CurrentUserResponse, ProjectResponse, WorkspaceResponse } from '@/lib/api/generated/model'

/** The signed-in User every test starts from. */
export function currentUser(overrides: Partial<CurrentUserResponse> = {}): CurrentUserResponse {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    email: 'creator@example.com',
    displayName: 'Rin Creator',
    avatarUrl: null,
    sessionId: '22222222-2222-4222-8222-222222222222',
    recentAuthentication: true,
    ...overrides,
  }
}

/** One Workspace the signed-in User belongs to. */
export function workspace(overrides: Partial<WorkspaceResponse> = {}): WorkspaceResponse {
  return {
    id: '33333333-3333-4333-8333-333333333333',
    name: 'Rin Creator',
    slug: 'rin-creator',
    kind: 'personal',
    status: 'active',
    publishingRolePolicy: 'owner_admin_editor',
    role: 'owner',
    createdAt: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

/** One Project inside a Workspace. */
export function project(overrides: Partial<ProjectResponse> = {}): ProjectResponse {
  return {
    id: '44444444-4444-4444-8444-444444444444',
    workspaceId: workspace().id,
    name: 'Episode 12',
    status: 'created',
    sourceKind: 'upload',
    createdAt: '2026-02-01T00:00:00+00:00',
    updatedAt: '2026-02-01T00:00:00+00:00',
    ...overrides,
  }
}
