import 'fake-indexeddb/auto'

import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { UploadPanel } from '@/features/uploads/UploadPanel'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'
import {
  MAX_CONCURRENT_PARTS,
  MAX_PART_BYTES,
  MAX_UPLOAD_BYTES,
  MIN_PART_BYTES,
  UploadCanceledError,
  UploadRejectedError,
  checkpointKey,
  indexedDbCheckpointStore,
  uploadSource,
  type CheckpointStore,
  type UploadApi,
  type UploadCheckpoint,
  type UploadFile,
} from '@/features/uploads/uploader'
import { ApiError } from '@/lib/api/client'

import { renderWithApi, stubApi, type Handler, type StubbedApi } from './support/api'
import { FakeEventSource } from './support/events'
import { currentUser, workspace } from './support/fixtures'

const ME = 'GET /api/v1/me'
const WORKSPACES = 'GET /api/v1/workspaces'
const PROJECT_ID = '44444444-4444-4444-8444-444444444444'
const UPLOAD_ID = '66666666-6666-4666-8666-666666666666'
const JOB_ID = '88888888-8888-4888-8888-888888888888'
const WORKSPACE_ID = workspace().id
const CREATE_UPLOAD = `POST /api/v1/projects/${PROJECT_ID}/uploads`
const COMPLETE_UPLOAD = `POST /api/v1/projects/${PROJECT_ID}/uploads/${UPLOAD_ID}/complete`
const START_ANALYSIS = `POST /api/v1/projects/${PROJECT_ID}/analysis`
const START_IMPORT = `POST /api/v1/projects/${PROJECT_ID}/youtube-imports`
const CANCEL_JOB = `POST /api/v1/jobs/${JOB_ID}/cancel`

vi.mock('next/navigation', () => ({
  usePathname: () => `/dashboard/projects/${PROJECT_ID}`,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}))

/** One source file, described the way the uploader reads a file the member picked. */
function sourceFile(size: number, name = 'episode.mp4'): UploadFile {
  return {
    name,
    type: 'video/mp4',
    size,
    lastModified: 1_700_000_000_000,
    slice: (start, end) =>
      new Blob([new Uint8Array(Math.max(0, Math.min(end, size) - start))], { type: 'video/mp4' }),
  }
}

/** A checkpoint store that keeps its rows for exactly as long as one test runs. */
function memoryStore(): CheckpointStore & { rows: Map<string, UploadCheckpoint> } {
  const rows = new Map<string, UploadCheckpoint>()
  return {
    rows,
    load: async (key) => rows.get(key) ?? null,
    save: async (key, checkpoint) => {
      rows.set(key, checkpoint)
    },
    forget: async (key) => {
      rows.delete(key)
    },
  }
}

/** The backend's upload endpoints, recorded rather than reached. */
function uploadApi(overrides: Partial<UploadApi> = {}): UploadApi & { signed: number[] } {
  const signed: number[] = []
  return {
    signed,
    createUpload: async () => ({ uploadId: UPLOAD_ID }),
    signPart: async ({ partNumber }) => {
      signed.push(partNumber)
      return { url: `https://objects.test/part/${partNumber}?signature=secret` }
    },
    completeUpload: async () => undefined,
    abortUpload: async () => undefined,
    ...overrides,
  }
}

/** The stubbed backend a signed-in member's upload panel talks to. */
function signedInApi(extra: Record<string, Handler> = {}): StubbedApi {
  return stubApi({
    [ME]: { body: currentUser() },
    [WORKSPACES]: { body: { workspaces: [workspace()] } },
    [CREATE_UPLOAD]: { status: 201, body: { id: UPLOAD_ID, expiresAt: '2026-02-02T00:00:00+00:00' } },
    [`POST /api/v1/projects/${PROJECT_ID}/uploads/${UPLOAD_ID}/parts/1`]: {
      body: {
        partNumber: 1,
        url: 'https://objects.test/part/1?signature=secret',
        expiresAt: '2026-02-01T00:05:00+00:00',
      },
    },
    'PUT /part/1': { status: 200, body: null, headers: { etag: '"part-one"' } },
    [COMPLETE_UPLOAD]: {
      body: {
        id: UPLOAD_ID,
        contentType: 'video/mp4',
        contentLength: 1024,
        downloadUrl: 'https://objects.test/download?signature=secret',
        downloadExpiresAt: '2026-02-01T00:05:00+00:00',
      },
    },
    [START_ANALYSIS]: { status: 202, body: { jobId: JOB_ID, status: 'queued' } },
    ...extra,
  })
}

/** Render the panel inside the Workspace the member is viewing. */
function renderPanel() {
  return renderWithApi(
    <WorkspaceProvider>
      <UploadPanel projectId={PROJECT_ID} />
    </WorkspaceProvider>,
  )
}

/** One Workspace-wide job frame, as the backend writes it. */
function jobEvent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    jobId: JOB_ID,
    projectId: PROJECT_ID,
    kind: 'ingest',
    status: 'running',
    stage: 'download',
    progress: 0.2,
    attempt: 1,
    errorCode: null,
    sequence: 2,
    ...overrides,
  }
}

/** Wait until the panel has opened its live connection, then answer as the backend. */
async function openStream(): Promise<FakeEventSource> {
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
  const stream = FakeEventSource.instances[0]
  if (stream === undefined) {
    throw new Error('the panel opened no stream')
  }
  return stream
}

beforeEach(() => {
  FakeEventSource.instances = []
  window.sessionStorage.clear()
  vi.stubGlobal('EventSource', FakeEventSource)
})

describe('the resumable uploader', () => {
  test('refuses a file larger than the backend accepts before it asks for anything', async () => {
    const api = uploadApi()

    const failure = await uploadSource({
      file: sourceFile(MAX_UPLOAD_BYTES + 1),
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api,
      store: memoryStore(),
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(UploadRejectedError)
    expect((failure as UploadRejectedError).code).toBe('FILE_TOO_LARGE')
    expect(api.signed).toHaveLength(0)
  })

  test('refuses an empty file, which no part could ever describe', async () => {
    const failure = await uploadSource({
      file: sourceFile(0),
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api: uploadApi(),
      store: memoryStore(),
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(UploadRejectedError)
    expect((failure as UploadRejectedError).code).toBe('EMPTY_FILE')
  })

  test('sends parts within the agreed size band and never more than three at once', async () => {
    const api = uploadApi()
    const sizes: number[] = []
    let inFlight = 0
    let peak = 0

    await uploadSource({
      file: sourceFile(MIN_PART_BYTES * 7),
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api,
      store: memoryStore(),
      putPart: async (_url, body) => {
        inFlight += 1
        peak = Math.max(peak, inFlight)
        sizes.push(body.size)
        await Promise.resolve()
        inFlight -= 1
        return '"etag"'
      },
    })

    expect(MAX_CONCURRENT_PARTS).toBe(3)
    expect(peak).toBe(3)
    expect([...api.signed].sort((left, right) => left - right)).toEqual([1, 2, 3, 4, 5, 6, 7])
    for (const size of sizes.slice(0, -1)) {
      expect(size).toBeGreaterThanOrEqual(MIN_PART_BYTES)
      expect(size).toBeLessThanOrEqual(MAX_PART_BYTES)
    }
  })

  test('retries a part that failed, waiting longer before each further attempt', async () => {
    const waits: number[] = []
    let attempts = 0

    await uploadSource({
      file: sourceFile(1024),
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api: uploadApi(),
      store: memoryStore(),
      sleep: async (ms) => {
        waits.push(ms)
      },
      putPart: async () => {
        attempts += 1
        if (attempts < 3) {
          throw new Error('the object store hung up')
        }
        return '"etag"'
      },
    })

    expect(attempts).toBe(3)
    expect(waits).toHaveLength(2)
    expect(waits[1]).toBeGreaterThan(waits[0] ?? 0)
  })

  test('stops retrying eventually and keeps the checkpoint so a later attempt can resume', async () => {
    const store = memoryStore()

    const failure = await uploadSource({
      file: sourceFile(MIN_PART_BYTES * 2),
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api: uploadApi(),
      store,
      sleep: async () => undefined,
      putPart: async () => {
        throw new Error('the object store hung up')
      },
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(Error)
    expect(store.rows.size).toBe(1)
  })

  test('resumes after a refresh, signing only the parts the checkpoint never recorded', async () => {
    const file = sourceFile(MIN_PART_BYTES * 3)
    const store = memoryStore()
    const first = uploadApi()
    let sent = 0
    await uploadSource({
      file,
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api: first,
      store,
      sleep: async () => undefined,
      putPart: async () => {
        sent += 1
        if (sent > 1) {
          throw new Error('the browser went away')
        }
        return '"etag"'
      },
    }).catch(() => undefined)
    expect(store.rows.get(checkpointKey({ workspaceId: WORKSPACE_ID, projectId: PROJECT_ID, file }))?.parts).toHaveLength(1)

    const second = uploadApi()
    await uploadSource({
      file,
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api: second,
      store,
      putPart: async () => '"etag"',
    })

    expect(second.signed).toEqual([2, 3])
  })

  test('keeps no signed URL anywhere in what it persists', async () => {
    const file = sourceFile(MIN_PART_BYTES * 2)
    const store = memoryStore()
    let sent = 0

    await uploadSource({
      file,
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api: uploadApi(),
      store,
      sleep: async () => undefined,
      putPart: async () => {
        sent += 1
        if (sent > 1) {
          throw new Error('stop here, with something already written down')
        }
        return '"etag"'
      },
    }).catch(() => undefined)

    const written = JSON.stringify([...store.rows.values()])
    expect(written).toContain(UPLOAD_ID)
    expect(written).not.toContain('http')
    expect(written).not.toContain('signature')
  })

  test('carries a checkpoint across a browser refresh through IndexedDB', async () => {
    const file = sourceFile(MIN_PART_BYTES * 2)
    const key = checkpointKey({ workspaceId: WORKSPACE_ID, projectId: PROJECT_ID, file })

    await indexedDbCheckpointStore().save(key, {
      uploadId: UPLOAD_ID,
      partSize: MIN_PART_BYTES,
      parts: [{ partNumber: 1, etag: '"part-one"' }],
    })
    const api = uploadApi({ createUpload: async () => ({ uploadId: 'a-second-upload' }) })
    await uploadSource({
      file,
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api,
      putPart: async () => '"part-two"',
    })

    expect(api.signed).toEqual([2])
    expect(await indexedDbCheckpointStore().load(key)).toBeNull()
  })

  test('aborts the upload with the backend when the member cancels it', async () => {
    const aborted: string[] = []
    const store = memoryStore()
    const controller = new AbortController()

    const failure = await uploadSource({
      file: sourceFile(MIN_PART_BYTES * 2),
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api: uploadApi({
        abortUpload: async ({ uploadId }) => {
          aborted.push(uploadId)
        },
      }),
      store,
      signal: controller.signal,
      putPart: async () => {
        controller.abort()
        throw new UploadCanceledError()
      },
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(UploadCanceledError)
    expect(aborted).toEqual([UPLOAD_ID])
    expect(store.rows.size).toBe(0)
  })

  test('forgets the checkpoint when the backend refuses the finished media', async () => {
    const store = memoryStore()

    const failure = await uploadSource({
      file: sourceFile(1024),
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api: uploadApi({
        completeUpload: async () => {
          throw new ApiError({
            status: 422,
            code: 'VALIDATION_ERROR',
            message: 'Request validation failed.',
            requestId: 'request-1234',
          })
        },
      }),
      store,
      putPart: async () => '"etag"',
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).status).toBe(422)
    expect(store.rows.size).toBe(0)
  })

  test('forgets the checkpoint once the upload has been completed', async () => {
    const file = sourceFile(1024)
    const store = memoryStore()

    await uploadSource({
      file,
      projectId: PROJECT_ID,
      workspaceId: WORKSPACE_ID,
      api: uploadApi(),
      store,
      putPart: async () => '"etag"',
    })

    expect(store.rows.size).toBe(0)
  })
})

describe('the media submission panel', () => {
  test('offers direct upload first and the public import only as a convenience', async () => {
    signedInApi()

    renderPanel()

    const upload = await screen.findByRole('region', { name: /upload a video/i })
    const importer = screen.getByRole('region', { name: /import from youtube/i })
    expect(upload.compareDocumentPosition(importer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(importer).toHaveTextContent(/convenience connector/i)
  })

  test('states the import policy instead of implying private videos will work', async () => {
    signedInApi()

    renderPanel()

    const importer = await screen.findByRole('region', { name: /import from youtube/i })
    expect(importer).toHaveTextContent(/public/i)
    expect(importer).toHaveTextContent(/right to use/i)
  })

  test('offers no cookie upload while the authenticated connector stays switched off', async () => {
    signedInApi()

    renderPanel()

    await screen.findByRole('region', { name: /import from youtube/i })
    expect(screen.queryByLabelText(/cookie/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/cookies\.txt/i)).not.toBeInTheDocument()
  })

  test('hides the submission controls from a member whose role cannot write', async () => {
    stubApi({
      [ME]: { body: currentUser() },
      [WORKSPACES]: { body: { workspaces: [workspace({ role: 'viewer' })] } },
    })

    renderPanel()

    expect(await screen.findByText(/only editors/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/video file/i)).not.toBeInTheDocument()
  })

  test('refuses a file the backend would refuse, before uploading a single byte', async () => {
    const user = userEvent.setup()
    const api = signedInApi()

    renderPanel()
    const field = await screen.findByLabelText(/video file/i)
    await user.upload(field, oversizeFile())

    expect(await screen.findByRole('alert')).toHaveTextContent(/too large/i)
    expect(api.calls.some((call) => call.path.endsWith('/uploads'))).toBe(false)
  })

  test('uploads the picked file and leaves the pipeline to start the work', async () => {
    const user = userEvent.setup()
    const api = signedInApi()

    renderPanel()
    await user.upload(await screen.findByLabelText(/video file/i), realFile())

    // Waiting for the settled state, not for the request, so the assertion below cannot
    // pass merely by running before the panel had a chance to misbehave.
    await screen.findByRole('button', { name: /find moments again/i })
    expect(api.calls.some((call) => call.path.endsWith(`/uploads/${UPLOAD_ID}/complete`))).toBe(
      true,
    )
    // Completing the upload is what starts ingest, and the backend refuses an analysis
    // that early. Asking for one here produced a 409 on every upload.
    expect(api.calls.some((call) => call.path.endsWith('/analysis'))).toBe(false)
    expect(await screen.findByRole('status')).toHaveTextContent(/queued|waiting/i)
  })

  test('names each stage the work is really in, up to a project that is ready', async () => {
    const user = userEvent.setup()
    signedInApi()

    renderPanel()
    await user.upload(await screen.findByLabelText(/video file/i), realFile())
    const stream = await openStream()

    act(() => stream.emit('progress', jobEvent({ kind: 'ingest' })))
    expect(await screen.findByText(/importing media/i)).toBeInTheDocument()

    act(() => stream.emit('progress', jobEvent({ kind: 'transcribe' })))
    expect(await screen.findByText(/transcribing/i)).toBeInTheDocument()

    act(() => stream.emit('progress', jobEvent({ kind: 'analyze' })))
    expect(await screen.findByText(/finding moments/i)).toBeInTheDocument()

    act(() => stream.emit('succeeded', jobEvent({ kind: 'analyze', status: 'succeeded', progress: 1 })))
    expect(await screen.findByText(/ready to review/i)).toBeInTheDocument()
  })

  test('distinguishes a retry, a cancellation, and a failure from one another', async () => {
    const user = userEvent.setup()
    signedInApi()

    renderPanel()
    await user.upload(await screen.findByLabelText(/video file/i), realFile())
    const stream = await openStream()

    act(() => stream.emit('retrying', jobEvent({ status: 'retrying', attempt: 2 })))
    expect(await screen.findByText(/retrying/i)).toBeInTheDocument()

    act(() => stream.emit('canceled', jobEvent({ status: 'canceled' })))
    expect(await screen.findByText(/canceled/i)).toBeInTheDocument()

    act(() => stream.emit('failed', jobEvent({ status: 'failed', errorCode: 'SOURCE_UNAVAILABLE' })))
    expect(await screen.findByText(/failed/i)).toBeInTheDocument()
  })

  test('asks the backend to cancel the job the member is watching', async () => {
    const user = userEvent.setup()
    const api = signedInApi({ [CANCEL_JOB]: { body: { id: JOB_ID, status: 'cancel_requested' } } })

    renderPanel()
    await user.upload(await screen.findByLabelText(/video file/i), realFile())
    const stream = await openStream()
    act(() => stream.emit('progress', jobEvent()))

    await user.click(await screen.findByRole('button', { name: /stop this job/i }))

    await waitFor(() =>
      expect(api.calls.some((call) => call.path === `/api/v1/jobs/${JOB_ID}/cancel`)).toBe(true),
    )
  })

  test('says it is reconnecting when the stream drops, without forgetting the job', async () => {
    const user = userEvent.setup()
    signedInApi()

    renderPanel()
    await user.upload(await screen.findByLabelText(/video file/i), realFile())
    const stream = await openStream()
    act(() => stream.emit('progress', jobEvent()))
    await screen.findByText(/importing media/i)

    act(() => stream.fail())
    expect(await screen.findByText(/reconnecting/i)).toBeInTheDocument()
    expect(screen.getByText(/importing media/i)).toBeInTheDocument()

    act(() => stream.emit('progress', jobEvent({ sequence: 3 })))
    await waitFor(() => expect(screen.queryByText(/reconnecting/i)).not.toBeInTheDocument())
    expect(screen.getAllByText(/importing media/i)).toHaveLength(1)
  })

  test('asks for analysis under one key however many times a member retries it', async () => {
    const user = userEvent.setup()
    const api = signedInApi()

    renderPanel()
    await user.upload(await screen.findByLabelText(/video file/i), realFile())
    const retry = await screen.findByRole('button', { name: /find moments again/i })
    await user.click(retry)
    await waitFor(() =>
      expect(api.calls.filter((call) => call.path.endsWith('/analysis'))).toHaveLength(1),
    )
    await user.click(screen.getByRole('button', { name: /find moments again/i }))

    await waitFor(() =>
      expect(api.calls.filter((call) => call.path.endsWith('/analysis'))).toHaveLength(2),
    )
    const keys = api.calls
      .filter((call) => call.path.endsWith('/analysis'))
      .map((call) => call.headers.get('Idempotency-Key'))
    expect(keys[0]).toBe(keys[1])
    expect(keys[0]).not.toBeNull()
  })
})

describe('the public YouTube import form', () => {
  test('refuses a host that is not YouTube without asking the backend', async () => {
    const user = userEvent.setup()
    const api = signedInApi({ [START_IMPORT]: { status: 202, body: {} } })

    renderPanel()
    await user.type(await screen.findByLabelText(/youtube video url/i), 'https://vimeo.com/12345')
    await user.click(screen.getByRole('button', { name: /import video/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/youtube/i)
    expect(api.calls.some((call) => call.path.endsWith('/youtube-imports'))).toBe(false)
  })

  test('refuses a URL that is not carried over HTTPS', async () => {
    const user = userEvent.setup()
    const api = signedInApi({ [START_IMPORT]: { status: 202, body: {} } })

    renderPanel()
    await user.type(await screen.findByLabelText(/youtube video url/i), 'http://www.youtube.com/watch?v=abc')
    await user.click(screen.getByRole('button', { name: /import video/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/https/i)
    expect(api.calls.some((call) => call.path.endsWith('/youtube-imports'))).toBe(false)
  })

  test('shows the backend refusal for a video that is not publicly accessible', async () => {
    const user = userEvent.setup()
    signedInApi({
      [START_IMPORT]: { status: 422, body: errorEnvelope('SOURCE_PRIVATE', 'This video is not publicly accessible.') },
    })

    renderPanel()
    await user.type(await screen.findByLabelText(/youtube video url/i), 'https://youtu.be/abcdefghijk')
    await user.click(screen.getByRole('button', { name: /import video/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/not publicly accessible/i)
  })

  test('shows the backend refusal for a source form it does not support', async () => {
    const user = userEvent.setup()
    signedInApi({
      [START_IMPORT]: {
        status: 422,
        body: errorEnvelope('SOURCE_UNSUPPORTED', 'This public video source is not supported.'),
      },
    })

    renderPanel()
    await user.type(
      await screen.findByLabelText(/youtube video url/i),
      'https://www.youtube.com/playlist?list=PL1',
    )
    await user.click(screen.getByRole('button', { name: /import video/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/not supported/i)
  })

  test('follows the import job the backend admitted', async () => {
    const user = userEvent.setup()
    signedInApi({
      [START_IMPORT]: {
        status: 202,
        body: { sourceImportId: UPLOAD_ID, jobId: JOB_ID, status: 'queued' },
      },
    })

    renderPanel()
    await user.type(await screen.findByLabelText(/youtube video url/i), 'https://www.youtube.com/watch?v=abcdefghijk')
    await user.click(screen.getByRole('button', { name: /import video/i }))

    const stream = await openStream()
    act(() => stream.emit('progress', jobEvent({ kind: 'source_import' })))
    expect(await screen.findByText(/importing media/i)).toBeInTheDocument()
  })

  test('submits the same URL twice under one key, so the backend can collapse it', async () => {
    const user = userEvent.setup()
    const api = signedInApi({
      [START_IMPORT]: {
        status: 202,
        body: { sourceImportId: UPLOAD_ID, jobId: JOB_ID, status: 'queued' },
      },
    })

    renderPanel()
    const field = await screen.findByLabelText(/youtube video url/i)
    await user.type(field, 'https://www.youtube.com/watch?v=abcdefghijk')
    await user.click(screen.getByRole('button', { name: /import video/i }))
    await waitFor(() =>
      expect(api.calls.filter((call) => call.path.endsWith('/youtube-imports'))).toHaveLength(1),
    )
    await user.click(screen.getByRole('button', { name: /import video/i }))

    await waitFor(() =>
      expect(api.calls.filter((call) => call.path.endsWith('/youtube-imports'))).toHaveLength(2),
    )
    const keys = api.calls
      .filter((call) => call.path.endsWith('/youtube-imports'))
      .map((call) => call.headers.get('Idempotency-Key'))
    expect(keys[0]).toBe(keys[1])
    expect(keys[0]).not.toBeNull()
  })
})

/** A file small enough that one part carries it, for the panel's own behaviour. */
function realFile(): File {
  return new File([new Uint8Array(1024)], 'episode.mp4', { type: 'video/mp4' })
}

/** A file the browser must refuse without asking, described rather than allocated. */
function oversizeFile(): File {
  const file = new File([new Uint8Array(8)], 'huge.mp4', { type: 'video/mp4' })
  Object.defineProperty(file, 'size', { value: MAX_UPLOAD_BYTES + 1 })
  return file
}

/** The sanitized envelope the backend answers a refused import with. */
function errorEnvelope(code: string, message: string): unknown {
  return { error: { code, message, requestId: 'request-1234' } }
}
