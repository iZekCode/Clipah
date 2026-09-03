import type {
  CandidateResponse,
  CompositionV1,
  EditResponse,
  CurrentUserResponse,
  ProjectResponse,
  WorkspaceResponse,
} from '@/lib/api/generated/model'

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

/** One exposed Clip Candidate, as the ranked review endpoint returns it. */
export function candidate(overrides: Partial<CandidateResponse> = {}): CandidateResponse {
  return {
    id: '55555555-5555-4555-8555-555555555551',
    projectId: project().id,
    rank: 1,
    score: 0.91,
    hook: 'The surprising opening',
    payoff: 'The useful resolution',
    reason: 'A complete and useful moment',
    category: 'insight',
    tags: ['creator', 'workflow'],
    startMs: 1_000,
    endMs: 31_000,
    durationMs: 30_000,
    transcriptExcerpt: 'A complete and useful moment.',
    contextDependencies: ['The speaker is discussing editing.'],
    scoreBreakdown: {
      hook: 0.9,
      payoff: 0.92,
      narrativeCompleteness: 0.93,
      contextSafety: 0.94,
      platformFit: 0.88,
      transcriptConfidence: 0.97,
      visualOpportunity: 0.8,
    },
    contextWarnings: ['Needs a source overlay.'],
    visualOpportunities: ['Show the workflow chart.'],
    createdAt: '2026-02-01T00:00:00+00:00',
    ...overrides,
  }
}

/** One caption word as the first Revision of an Edit carries it. */
function captionWord(id: string, startMs: number, text: string): CompositionV1['captions']['words'][number] {
  return { id, startMs, endMs: startMs + 900, text, speaker: 'SPEAKER_00' }
}

/** One composition, shaped exactly as `create_edit_from_candidate` produces it. */
export function composition(overrides: Partial<CompositionV1> = {}): CompositionV1 {
  return {
    schemaVersion: 1,
    sourceAssetId: '66666666-6666-4666-8666-666666666666',
    durationMs: 30_000,
    canvas: { width: 1080, height: 1920, background: '#000000' },
    sourceRange: { inMs: 1_000, outMs: 31_000 },
    template: null,
    brandKit: null,
    tracks: [
      {
        id: 'main-video',
        type: 'video',
        items: [
          {
            id: 'scene-1',
            sourceAssetId: '66666666-6666-4666-8666-666666666666',
            timelineStartMs: 0,
            sourceInMs: 1_000,
            sourceOutMs: 31_000,
            transform: { x: 0.5, y: 0.5, scale: 1, rotation: 0 },
            crop: null,
            opacity: 1,
            blendMode: 'normal',
            motion: 'none',
            origin: { type: 'source', suggestionId: null, provenanceId: null },
            keyframes: [],
          },
        ],
      },
    ],
    captions: {
      mode: 'karaoke',
      words: [
        captionWord('w000001', 0, 'Ini'),
        captionWord('w000002', 1_000, 'cara'),
        captionWord('w000003', 12_000, 'kerja'),
        captionWord('w000004', 20_000, 'editornya'),
      ],
      style: {
        fontFamily: 'Montserrat',
        fontSize: 64,
        color: '#FFFFFF',
        highlightColor: '#FFD166',
        align: 'center',
        weight: 700,
        italic: false,
        decoration: 'none',
        letterSpacing: 0,
        lineHeight: 1.2,
        backgroundEnabled: false,
        backgroundColor: '#000000',
      },
    },
    overlays: [],
    audio: { gainDb: 0, musicGainDb: -18 },
    bookmarks: [],
    ...overrides,
  }
}

/** One Edit, as `GET /api/v1/edits/{id}` returns it. */
export function edit(overrides: Partial<EditResponse> = {}): EditResponse {
  return {
    id: '77777777-7777-4777-8777-777777777777',
    projectId: project().id,
    candidateId: candidate().id,
    currentRevision: 1,
    composition: composition(),
    compositionHash: 'a'.repeat(64),
    createdAt: '2026-02-01T00:00:00+00:00',
    updatedAt: '2026-02-01T00:00:00+00:00',
    ...overrides,
  }
}
