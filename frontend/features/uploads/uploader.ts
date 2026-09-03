/**
 * Move one source file into object storage in parts, and survive losing the browser.
 *
 * The backend hands out one five-minute capability per part, so nothing durable here may
 * outlive it: the checkpoint records the upload's identity and the ETags the object store
 * already acknowledged, and every signed URL is asked for again at the moment it is used.
 * That is what makes a resumed upload safe — a checkpoint restored from IndexedDB grants
 * no access on its own.
 *
 * Nothing in this module reaches for React, so its rules can be tested as rules.
 */

import { ApiError } from '@/lib/api/client'
import {
  abortApiV1ProjectsProjectIdUploadsUploadIdDelete,
  completeApiV1ProjectsProjectIdUploadsUploadIdCompletePost,
  createApiV1ProjectsProjectIdUploadsPost,
  signPartRouteApiV1ProjectsProjectIdUploadsUploadIdPartsPartNumberPost,
} from '@/lib/api/generated/uploads/uploads'

/** The part-size band the plan fixed, and the ceiling the backend enforces. */
export const MIN_PART_BYTES = 8 * 1024 * 1024
export const MAX_PART_BYTES = 32 * 1024 * 1024
export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
export const MAX_PART_COUNT = 10_000
export const MAX_CONCURRENT_PARTS = 3
export const PART_ATTEMPTS = 4
const RETRY_BASE_MS = 500
const DATABASE_NAME = 'clipah-uploads'
const CHECKPOINT_STORE = 'checkpoints'

/** Why a file was refused before any request was made. */
export type UploadRejectionCode = 'FILE_TOO_LARGE' | 'EMPTY_FILE'

/** A file this browser will not offer the backend, and the reason a member can read. */
export class UploadRejectedError extends Error {
  readonly code: UploadRejectionCode

  constructor(code: UploadRejectionCode, message: string) {
    super(message)
    this.name = 'UploadRejectedError'
    this.code = code
  }
}

/** The member stopped the upload; the provider upload was abandoned with the backend. */
export class UploadCanceledError extends Error {
  constructor() {
    super('The upload was canceled.')
    this.name = 'UploadCanceledError'
  }
}

/** What the uploader needs of a picked file, which a browser `File` already provides. */
export interface UploadFile {
  name: string
  type: string
  size: number
  lastModified: number
  slice(start: number, end: number): Blob
}

/** One part the object store has already acknowledged. */
export interface UploadedPart {
  partNumber: number
  etag: string
}

/**
 * Everything a resumed upload may remember.
 *
 * There is deliberately no room in this shape for a signed URL: what is written down
 * names an upload and the parts it already holds, and nothing else.
 */
export interface UploadCheckpoint {
  uploadId: string
  partSize: number
  parts: UploadedPart[]
}

/** Where checkpoints live between one page load and the next. */
export interface CheckpointStore {
  load(key: string): Promise<UploadCheckpoint | null>
  save(key: string, checkpoint: UploadCheckpoint): Promise<void>
  forget(key: string): Promise<void>
}

/** The backend endpoints one upload passes through. */
export interface UploadApi {
  createUpload(input: {
    projectId: string
    workspaceId: string
    filename: string
    contentType: string
    contentLength: number
  }): Promise<{ uploadId: string }>
  signPart(input: {
    projectId: string
    workspaceId: string
    uploadId: string
    partNumber: number
  }): Promise<{ url: string }>
  completeUpload(input: {
    projectId: string
    workspaceId: string
    uploadId: string
    parts: UploadedPart[]
  }): Promise<void>
  abortUpload(input: {
    projectId: string
    workspaceId: string
    uploadId: string
  }): Promise<void>
}

/** Send one part's bytes to the capability the backend just issued. */
export type PartSender = (url: string, body: Blob, signal?: AbortSignal) => Promise<string>

/** One upload, and the collaborators a test pins so the rules stay observable. */
export interface UploadRequest {
  file: UploadFile
  projectId: string
  workspaceId: string
  api?: UploadApi
  store?: CheckpointStore
  signal?: AbortSignal
  onProgress?: (uploadedBytes: number, totalBytes: number) => void
  putPart?: PartSender
  sleep?: (milliseconds: number) => Promise<void>
}

/** What the caller learns once the object store holds the whole file. */
export interface UploadResult {
  uploadId: string
}

/**
 * Name the file this checkpoint belongs to.
 *
 * Identity is the Workspace, the Project, and the file as the operating system describes
 * it, so picking the same file again after a refresh resumes, and picking a different one
 * starts over.
 */
export function checkpointKey({
  workspaceId,
  projectId,
  file,
}: {
  workspaceId: string
  projectId: string
  file: UploadFile
}): string {
  return [workspaceId, projectId, file.name, file.size, file.lastModified].join('/')
}

/** Choose the smallest part in the agreed band that keeps the part count legal. */
export function choosePartSize(contentLength: number): number {
  let size = MIN_PART_BYTES
  while (Math.ceil(contentLength / size) > MAX_PART_COUNT && size < MAX_PART_BYTES) {
    size = Math.min(size * 2, MAX_PART_BYTES)
  }
  return size
}

/**
 * Upload one file, resuming whatever an earlier attempt already finished.
 *
 * A part is signed inside the attempt that uses it, so a retry after a long backoff asks
 * for a fresh capability rather than replaying an expired one.
 */
export async function uploadSource(request: UploadRequest): Promise<UploadResult> {
  const {
    file,
    projectId,
    workspaceId,
    api = backendUploadApi(),
    store = indexedDbCheckpointStore(),
    signal,
    onProgress,
    putPart = putSignedPart,
    sleep = wait,
  } = request

  rejectUnusableFile(file)
  const key = checkpointKey({ workspaceId, projectId, file })
  const resumed = await store.load(key)
  const partSize = resumed?.partSize ?? choosePartSize(file.size)
  const uploadId =
    resumed?.uploadId ??
    (
      await api.createUpload({
        projectId,
        workspaceId,
        filename: file.name,
        contentType: file.type === '' ? 'application/octet-stream' : file.type,
        contentLength: file.size,
      })
    ).uploadId

  const done = new Map<number, string>(
    (resumed?.parts ?? []).map((part) => [part.partNumber, part.etag]),
  )
  const record = () => store.save(key, { uploadId, partSize, parts: sortedParts(done) })
  await record()
  onProgress?.(uploadedBytes(done, partSize, file.size), file.size)

  const pending = missingPartNumbers(file.size, partSize, done)
  try {
    await inParallel(pending, MAX_CONCURRENT_PARTS, async (partNumber) => {
      const etag = await withRetry(
        async () => {
          const { url } = await api.signPart({ projectId, workspaceId, uploadId, partNumber })
          return putPart(url, partBody(file, partNumber, partSize), signal)
        },
        { sleep, signal },
      )
      done.set(partNumber, etag)
      await record()
      onProgress?.(uploadedBytes(done, partSize, file.size), file.size)
    })

    await api.completeUpload({ projectId, workspaceId, uploadId, parts: sortedParts(done) })
  } catch (error) {
    await discardOn(error, { api, store, key, projectId, workspaceId, uploadId })
    throw error
  }

  await store.forget(key)
  return { uploadId }
}

/** The checkpoint store the browser actually keeps, which survives a page load. */
export function indexedDbCheckpointStore(): CheckpointStore {
  return {
    load: async (key) => {
      const database = await openDatabase()
      try {
        const found = await promised<UploadCheckpoint | undefined>(
          database.transaction(CHECKPOINT_STORE, 'readonly').objectStore(CHECKPOINT_STORE).get(key),
        )
        return found ?? null
      } finally {
        database.close()
      }
    },
    save: async (key, checkpoint) => {
      const database = await openDatabase()
      try {
        await promised(
          database
            .transaction(CHECKPOINT_STORE, 'readwrite')
            .objectStore(CHECKPOINT_STORE)
            .put(checkpoint, key),
        )
      } finally {
        database.close()
      }
    },
    forget: async (key) => {
      const database = await openDatabase()
      try {
        await promised(
          database
            .transaction(CHECKPOINT_STORE, 'readwrite')
            .objectStore(CHECKPOINT_STORE)
            .delete(key),
        )
      } finally {
        database.close()
      }
    },
  }
}

/** The upload endpoints, called through the one client that carries Session and CSRF. */
export function backendUploadApi(): UploadApi {
  return {
    createUpload: async ({ projectId, workspaceId, filename, contentType, contentLength }) => {
      const created = await createApiV1ProjectsProjectIdUploadsPost(
        projectId,
        { filename, contentType, contentLength },
        { workspace_id: workspaceId },
      )
      return { uploadId: requiredString(created['id'], 'upload identifier') }
    },
    signPart: async ({ projectId, workspaceId, uploadId, partNumber }) => {
      const signed = await signPartRouteApiV1ProjectsProjectIdUploadsUploadIdPartsPartNumberPost(
        projectId,
        uploadId,
        partNumber,
        { workspace_id: workspaceId },
      )
      return { url: requiredString(signed['url'], 'signed part URL') }
    },
    completeUpload: async ({ projectId, workspaceId, uploadId, parts }) => {
      await completeApiV1ProjectsProjectIdUploadsUploadIdCompletePost(
        projectId,
        uploadId,
        { parts },
        { workspace_id: workspaceId },
      )
    },
    abortUpload: async ({ projectId, workspaceId, uploadId }) => {
      await abortApiV1ProjectsProjectIdUploadsUploadIdDelete(projectId, uploadId, {
        workspace_id: workspaceId,
      })
    },
  }
}

/** Refuse here what the backend would refuse anyway, before a byte leaves the browser. */
function rejectUnusableFile(file: UploadFile): void {
  if (file.size === 0) {
    throw new UploadRejectedError('EMPTY_FILE', 'That file is empty.')
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new UploadRejectedError('FILE_TOO_LARGE', 'That file is too large to upload.')
  }
}

/** Put back what a failure means for the checkpoint and for the provider's upload. */
async function discardOn(
  error: unknown,
  context: {
    api: UploadApi
    store: CheckpointStore
    key: string
    projectId: string
    workspaceId: string
    uploadId: string
  },
): Promise<void> {
  const { api, store, key, projectId, workspaceId, uploadId } = context
  if (error instanceof UploadCanceledError) {
    await api.abortUpload({ projectId, workspaceId, uploadId }).catch(() => undefined)
    await store.forget(key)
    return
  }
  // A refusal is final: the backend has already discarded the object, so a resumed
  // checkpoint would only replay a rejection. Anything else may still succeed later.
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    await store.forget(key)
  }
}

/** Which parts still have to be sent, in the order the object store expects them. */
function missingPartNumbers(
  contentLength: number,
  partSize: number,
  done: Map<number, string>,
): number[] {
  const total = Math.ceil(contentLength / partSize)
  const missing: number[] = []
  for (let partNumber = 1; partNumber <= total; partNumber += 1) {
    if (!done.has(partNumber)) {
      missing.push(partNumber)
    }
  }
  return missing
}

/** The bytes of one part, sliced without reading the whole file into memory. */
function partBody(file: UploadFile, partNumber: number, partSize: number): Blob {
  const start = (partNumber - 1) * partSize
  return file.slice(start, Math.min(start + partSize, file.size))
}

/** How much of the file the object store already holds. */
function uploadedBytes(done: Map<number, string>, partSize: number, total: number): number {
  return Math.min(done.size * partSize, total)
}

/** The completed parts, ordered, as both the backend and the checkpoint want them. */
function sortedParts(done: Map<number, string>): UploadedPart[] {
  return [...done.entries()]
    .map(([partNumber, etag]) => ({ partNumber, etag }))
    .sort((left, right) => left.partNumber - right.partNumber)
}

/** Run the work with a fixed number of parts in flight, and stop at the first failure. */
async function inParallel<T>(
  items: T[],
  limit: number,
  work: (item: T) => Promise<void>,
): Promise<void> {
  const queue = [...items]
  const workers = Array.from({ length: Math.min(limit, queue.length) }, async () => {
    for (let next = queue.shift(); next !== undefined; next = queue.shift()) {
      await work(next)
    }
  })
  await Promise.all(workers)
}

/**
 * Try one part until it sticks, waiting longer between attempts each time.
 *
 * A refusal the backend already decided is not retried: repeating a 4xx only spends the
 * member's time. Cancellation ends the attempt immediately and says so plainly.
 */
async function withRetry<T>(
  attempt: () => Promise<T>,
  { sleep, signal }: { sleep: (milliseconds: number) => Promise<void>; signal?: AbortSignal },
): Promise<T> {
  let failure: unknown = new Error('The upload could not be completed.')
  for (let tries = 1; tries <= PART_ATTEMPTS; tries += 1) {
    if (signal?.aborted === true) {
      throw new UploadCanceledError()
    }
    try {
      return await attempt()
    } catch (error) {
      if (isCancellation(error, signal)) {
        throw new UploadCanceledError()
      }
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        throw error
      }
      failure = error
      if (tries < PART_ATTEMPTS) {
        await sleep(RETRY_BASE_MS * 2 ** (tries - 1))
      }
    }
  }
  throw failure
}

/** Whether this failure is the member stopping rather than the network misbehaving. */
function isCancellation(error: unknown, signal?: AbortSignal): boolean {
  if (error instanceof UploadCanceledError) {
    return true
  }
  if (signal?.aborted === true) {
    return true
  }
  return error instanceof DOMException && error.name === 'AbortError'
}

/** Send one part to the object store directly, carrying no cookie and no CSRF token. */
async function putSignedPart(url: string, body: Blob, signal?: AbortSignal): Promise<string> {
  const response = await fetch(url, { method: 'PUT', body, signal })
  if (!response.ok) {
    throw new Error(`The object store refused a part (${response.status}).`)
  }
  const etag = response.headers.get('etag')
  if (etag === null) {
    throw new Error('The object store acknowledged a part without an ETag.')
  }
  return etag
}

/** Wait, so a retry does not hammer a service that is already struggling. */
function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds)
  })
}

/** Open the one database checkpoints live in, creating its store on first use. */
function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 1)
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(CHECKPOINT_STORE)) {
        request.result.createObjectStore(CHECKPOINT_STORE)
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('IndexedDB is unavailable.'))
  })
}

/** Read one IndexedDB request as a promise. */
function promised<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('The checkpoint could not be read.'))
  })
}

/** Read one string the contract promises, refusing to guess when it is absent. */
function requiredString(value: unknown, what: string): string {
  if (typeof value !== 'string') {
    throw new Error(`The backend answered without a ${what}.`)
  }
  return value
}
