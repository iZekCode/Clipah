/**
 * Reviewing a clip before anyone edits it: why it ranked, what the cut risks, and how it
 * reads at other lengths.
 *
 * Everything on these surfaces was written by a language model reading someone else's
 * words, or typed by a member citing a source. So all of it is rendered as React children,
 * and every external link leaves isolated.
 */
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test } from 'vitest'

import { ContextWarnings } from '@/features/clips/ContextWarnings'
import { EvidencePanel } from '@/features/clips/EvidencePanel'
import { ScoreBreakdown } from '@/features/clips/ScoreBreakdown'
import { VariantLab } from '@/features/clips/VariantLab'
import type {
  ClaimEvidenceResponse,
  ClipVariantResponse,
  ContextWarningResponse,
} from '@/lib/api/generated/model'

import { renderWithApi, stubApi, type StubbedApi } from './support/api'
import { currentUser, project, workspace } from './support/fixtures'

const PROJECT_ID = project().id
const WORKSPACE_ID = workspace().id
const CANDIDATE_ID = '55555555-6666-4777-8888-999999999999'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const VARIANTS = `GET /api/v1/projects/${PROJECT_ID}/candidates/${CANDIDATE_ID}/variants`
const CREATE_VARIANTS = `POST /api/v1/projects/${PROJECT_ID}/candidates/${CANDIDATE_ID}/variants`
const EVIDENCE = `GET /api/v1/projects/${PROJECT_ID}/candidates/${CANDIDATE_ID}/claim-evidence`
const CREATE_EVIDENCE = `POST /api/v1/projects/${PROJECT_ID}/candidates/${CANDIDATE_ID}/claim-evidence`

/** One warning as the API reports it. */
function warning(overrides: Partial<ContextWarningResponse> = {}): ContextWarningResponse {
  return {
    type: 'omitted_caveat',
    severity: 'warning',
    evidenceWordIds: ['w000012'],
    suggestedStartWordId: null,
    suggestedEndWordId: 'w000020',
    ...overrides,
  }
}

/** One variant as the API reports it. */
function variant(overrides: Partial<ClipVariantResponse> = {}): ClipVariantResponse {
  return {
    id: '11111111-2222-4333-8444-555555555555',
    candidateId: CANDIDATE_ID,
    hookStrategy: 'cold_open',
    platform: 'tiktok',
    targetDurationMs: 30_000,
    startWordId: 'w000001',
    endWordId: 'w000030',
    startMs: 0,
    endMs: 30_000,
    durationMs: 30_000,
    title: 'The form was the ceiling',
    rationale: "Opens on the candidate's own first sentence. Runs 30s against a 30s target.",
    createdAt: '2026-09-01T00:00:00+00:00',
    warnings: [],
    packaging: {
      platform: 'tiktok',
      aspectRatio: '9:16',
      safeAreaTopPercent: 8,
      safeAreaBottomPercent: 22,
      safeAreaHorizontalPercent: 12,
      maxTitleCharacters: 150,
      captionStyle: 'karaoke_bold',
      exportPreset: '1080x1920',
    },
    ...overrides,
  }
}

/** One citation as the API reports it. */
function evidence(overrides: Partial<ClaimEvidenceResponse> = {}): ClaimEvidenceResponse {
  return {
    id: '99999999-8888-4777-8666-555555555555',
    candidateId: CANDIDATE_ID,
    startWordId: 'w000004',
    endWordId: 'w000009',
    claimText: 'Removing the form doubled activation',
    sourceUrl: 'https://example.test/report?page=2',
    sourceTitle: 'Quarterly activation report',
    publisher: 'Example Institute',
    retrievedAt: '2026-09-01T00:00:00+00:00',
    verificationStatus: 'unverified',
    createdByUserId: currentUser().id,
    createdAt: '2026-09-01T00:00:00+00:00',
    ...overrides,
  }
}

/** Stub the reads these surfaces make. */
function stubReview(overrides: Record<string, unknown> = {}): StubbedApi {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [VARIANTS]: { body: { variants: [variant()], supportedDurationsMs: [20_000, 30_000] } },
    [CREATE_VARIANTS]: {
      status: 201,
      body: { variants: [variant()], supportedDurationsMs: [20_000, 30_000] },
    },
    [EVIDENCE]: { body: { evidence: [evidence()] } },
    [CREATE_EVIDENCE]: { status: 201, body: evidence() },
    ...overrides,
  } as Parameters<typeof stubApi>[0])
}

describe('why this clip ranked where it did', () => {
  test('every score component is shown as a figure a member can weigh', () => {
    renderWithApi(
      <ScoreBreakdown
        score={0.82}
        breakdown={{
          narrative_completeness: 0.9,
          hook_strength: 0.7,
          context_safety: 0.6,
        }}
      />,
    )

    expect(screen.getByText(/82%/)).toBeVisible()
    expect(screen.getByText(/narrative completeness/i)).toBeVisible()
    expect(screen.getByText(/90%/)).toBeVisible()
  })

  test('a breakdown the analyzer never produced renders nothing rather than zeros', () => {
    renderWithApi(<ScoreBreakdown score={0.5} breakdown={{}} />)

    expect(screen.getByText(/no score breakdown/i)).toBeVisible()
  })
})

describe('what the cut risks', () => {
  test('each warning is shown with its severity and the words that prove it', () => {
    renderWithApi(<ContextWarnings warnings={[warning()]} />)

    const item = screen.getByRole('listitem')
    expect(within(item).getByText(/omitted caveat/i)).toBeVisible()
    expect(within(item).getByText(/warning/i)).toBeVisible()
    expect(within(item).getByText(/w000012/)).toBeVisible()
    expect(within(item).getByText(/w000020/)).toBeVisible()
  })

  test('a blocking warning is distinguished from an advisory one', () => {
    renderWithApi(
      <ContextWarnings
        warnings={[warning({ type: 'cut_off_question', severity: 'blocking' })]}
      />,
    )

    expect(screen.getByRole('listitem')).toHaveTextContent(/blocking/i)
  })

  test('a clip with nothing to flag says so plainly', () => {
    renderWithApi(<ContextWarnings warnings={[]} />)

    expect(screen.getByText(/no context warnings/i)).toBeVisible()
  })
})

describe('comparing readings of the same moment', () => {
  test('variants are listed with their length, hook, and reason', async () => {
    stubReview()
    renderWithApi(
      <VariantLab
        projectId={PROJECT_ID}
        candidateId={CANDIDATE_ID}
        workspaceId={WORKSPACE_ID}
        proxyUrl="https://cdn.test/proxy.mp4"
      />,
    )

    const item = await screen.findByRole('listitem')
    expect(within(item).getByText(/30s · cold open · tiktok/i)).toBeVisible()
    expect(within(item).getByText(/Opens on the candidate/)).toBeVisible()
  })

  test('every variant is compared against one proxy, never a second copy', async () => {
    stubReview({
      [VARIANTS]: {
        body: {
          variants: [variant(), variant({ id: 'b', hookStrategy: 'question_first' })],
          supportedDurationsMs: [20_000, 30_000],
        },
      },
    })
    renderWithApi(
      <VariantLab
        projectId={PROJECT_ID}
        candidateId={CANDIDATE_ID}
        workspaceId={WORKSPACE_ID}
        proxyUrl="https://cdn.test/proxy.mp4"
      />,
    )

    await screen.findAllByRole('listitem')
    expect(document.querySelectorAll('video')).toHaveLength(1)
  })

  test('choosing a variant seeks the one proxy to that variant', async () => {
    stubReview()
    const user = userEvent.setup()
    renderWithApi(
      <VariantLab
        projectId={PROJECT_ID}
        candidateId={CANDIDATE_ID}
        workspaceId={WORKSPACE_ID}
        proxyUrl="https://cdn.test/proxy.mp4"
      />,
    )

    await user.click(await screen.findByRole('button', { name: /preview/i }))

    const media = document.querySelector('video')
    expect(media).not.toBeNull()
    expect(media?.currentTime).toBe(0)
  })

  test('only the supported lengths may be asked for', async () => {
    stubReview()
    renderWithApi(
      <VariantLab
        projectId={PROJECT_ID}
        candidateId={CANDIDATE_ID}
        workspaceId={WORKSPACE_ID}
        proxyUrl="https://cdn.test/proxy.mp4"
      />,
    )

    const options = await screen.findAllByRole('checkbox')
    expect(options.map((option) => option.getAttribute('value'))).toEqual(['20000', '30000'])
  })

  test('a warning on a variant is shown beside it, not hidden behind a click', async () => {
    stubReview({
      [VARIANTS]: {
        body: {
          variants: [variant({ warnings: [warning()] })],
          supportedDurationsMs: [20_000],
        },
      },
    })
    renderWithApi(
      <VariantLab
        projectId={PROJECT_ID}
        candidateId={CANDIDATE_ID}
        workspaceId={WORKSPACE_ID}
        proxyUrl="https://cdn.test/proxy.mp4"
      />,
    )

    expect(await screen.findByText(/omitted caveat/i)).toBeVisible()
  })
})

describe('citing the source behind a claim', () => {
  test('a citation is listed with its publisher and an isolated link', async () => {
    stubReview()
    renderWithApi(
      <EvidencePanel
        projectId={PROJECT_ID}
        candidateId={CANDIDATE_ID}
        workspaceId={WORKSPACE_ID}
      />,
    )

    const link = await screen.findByRole('link', { name: /quarterly activation report/i })
    expect(link).toHaveAttribute('href', 'https://example.test/report?page=2')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'))
    expect(link).toHaveAttribute('target', '_blank')
  })

  test('an unverified citation says so rather than implying it was checked', async () => {
    stubReview()
    renderWithApi(
      <EvidencePanel
        projectId={PROJECT_ID}
        candidateId={CANDIDATE_ID}
        workspaceId={WORKSPACE_ID}
      />,
    )

    expect(await screen.findByText(/not verified/i)).toBeVisible()
  })

  test('markup in a citation is rendered as text, never as an element', async () => {
    stubReview({
      [EVIDENCE]: {
        body: {
          evidence: [evidence({ sourceTitle: '<img src=x onerror="alert(1)">report' })],
        },
      },
    })
    renderWithApi(
      <EvidencePanel
        projectId={PROJECT_ID}
        candidateId={CANDIDATE_ID}
        workspaceId={WORKSPACE_ID}
      />,
    )

    expect(await screen.findByText(/<img src=x/)).toBeVisible()
    expect(document.querySelector('img')).toBeNull()
  })

  test('a rejected source is explained rather than silently dropped', async () => {
    stubReview({
      [CREATE_EVIDENCE]: {
        status: 422,
        body: { error: { code: 'EVIDENCE_INVALID', message: 'no', requestId: 'r1' } },
      },
    })
    const user = userEvent.setup()
    renderWithApi(
      <EvidencePanel
        projectId={PROJECT_ID}
        candidateId={CANDIDATE_ID}
        workspaceId={WORKSPACE_ID}
      />,
    )

    await user.type(await screen.findByLabelText('Claim'), 'Growth doubled')
    await user.type(screen.getByLabelText('Source link'), 'http://example.test')
    await user.type(screen.getByLabelText('Title'), 'Report')
    await user.type(screen.getByLabelText('Publisher'), 'Example')
    await user.type(screen.getByLabelText('First word'), 'w000004')
    await user.type(screen.getByLabelText('Last word'), 'w000009')
    await user.click(screen.getByRole('button', { name: /attach source/i }))

    expect(await screen.findByRole('status')).toHaveTextContent(/could not be accepted/i)
  })
})
