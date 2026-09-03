/**
 * Autosave: one save in flight, one draft kept, and a state a member can read.
 *
 * A composition is saved against the Revision the browser believes is current. That is
 * the whole concurrency story: if the backend disagrees, this stops saving rather than
 * guessing which document should win, and hands the decision to the member. Nothing is
 * discarded on the way — an unsent change is kept where a reload can find it, because
 * the reasons a save fails (a dropped connection, a second editor) are exactly the
 * reasons a member would otherwise lose work.
 */
import { ApiError } from '@/lib/api/client'
import type { CompositionV1 } from '@/lib/api/generated/model'

/** How long the editor waits after the last change before saving. */
export const AUTOSAVE_DEBOUNCE_MS = 750

/** The stable code the backend refuses a stale save with. */
export const REVISION_CONFLICT_CODE = 'EDIT_REVISION_CONFLICT'

/** What the editor tells a member about their unsaved work. */
export type SaveStatus = 'idle' | 'saving' | 'saved' | 'offline' | 'conflict'

/** One accepted save, as the backend answers it. */
export interface SaveResult {
  currentRevision: number
  composition: CompositionV1
}

/** One composition that has not reached the backend, and what it was built on. */
export interface Draft {
  composition: CompositionV1
  baseRevision: number
}

/** Where an unsent draft waits for the next attempt, or the next visit. */
export interface DraftStore {
  load: (editId: string) => Draft | null
  save: (editId: string, draft: Draft) => void
  forget: (editId: string) => void
}

/** What one autosave needs from the world around it. */
export interface AutosaveOptions {
  editId: string
  revision: number
  save: (composition: CompositionV1, expectedRevision: number) => Promise<SaveResult>
  onStatus: (status: SaveStatus) => void
  onSaved: (result: SaveResult) => void
  onConflict: (currentRevision: number | null) => void
  store?: DraftStore
  isOnline?: () => boolean
}

/** A draft store that lives exactly as long as the page that made it. */
export function memoryDraftStore(): DraftStore {
  const rows = new Map<string, Draft>()
  return {
    load: (editId) => rows.get(editId) ?? null,
    save: (editId, draft) => {
      rows.set(editId, draft)
    },
    forget: (editId) => {
      rows.delete(editId)
    },
  }
}

/**
 * A draft store backed by this browser's local storage.
 *
 * A draft is a member's own unsent work on their own device, so it is kept where a
 * reload can find it and nowhere else. Storage that refuses to answer is treated as
 * storage that holds nothing, because losing the draft must never break the editor.
 */
export function localDraftStore(): DraftStore {
  const key = (editId: string) => `clipah.editor.draft.${editId}`
  return {
    load: (editId) => {
      try {
        const raw = window.localStorage.getItem(key(editId))
        return raw === null ? null : (JSON.parse(raw) as Draft)
      } catch {
        return null
      }
    },
    save: (editId, draft) => {
      try {
        window.localStorage.setItem(key(editId), JSON.stringify(draft))
      } catch {
        // A full or blocked store loses the draft, never the editing session.
      }
    },
    forget: (editId) => {
      try {
        window.localStorage.removeItem(key(editId))
      } catch {
        // Nothing to do: the draft is already unreachable.
      }
    },
  }
}

/** Debounced, single-flight saving of one Edit. */
export class Autosave {
  private readonly options: AutosaveOptions
  private readonly store: DraftStore
  private readonly isOnline: () => boolean
  private revision: number
  private pending: CompositionV1 | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private inFlight = false
  private blocked = false
  private state: SaveStatus = 'idle'

  constructor(options: AutosaveOptions) {
    this.options = options
    this.store = options.store ?? localDraftStore()
    this.isOnline = options.isOnline ?? (() => navigator.onLine)
    this.revision = options.revision
  }

  /** What the editor should be telling the member right now. */
  get status(): SaveStatus {
    return this.state
  }

  /** The Revision the next save will be made against. */
  get expectedRevision(): number {
    return this.revision
  }

  /** Record one change and save it once the member stops for a moment. */
  queue(composition: CompositionV1): void {
    this.pending = composition
    this.store.save(this.options.editId, { composition, baseRevision: this.revision })
    if (this.timer !== null) {
      clearTimeout(this.timer)
    }
    this.timer = setTimeout(() => {
      this.timer = null
      void this.run()
    }, AUTOSAVE_DEBOUNCE_MS)
  }

  /** Save whatever is pending now, without waiting for the debounce. */
  async flush(): Promise<void> {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    await this.run()
  }

  /** Try again after the connection came back. */
  async retryPending(): Promise<void> {
    await this.run()
  }

  /** Save against the Revision the backend now holds, after a conflict was resolved. */
  resume(revision: number): void {
    this.revision = revision
    this.blocked = false
  }

  /** Accept one Revision as the current one without sending anything. */
  accept(revision: number): void {
    this.revision = revision
    this.pending = null
    this.blocked = false
    this.store.forget(this.options.editId)
    this.announce('saved')
  }

  /** Stop every timer this autosave owns. */
  dispose(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }

  private async run(): Promise<void> {
    const composition = this.pending
    if (composition === null || this.blocked) {
      return
    }
    if (this.inFlight) {
      return
    }
    if (!this.isOnline()) {
      this.announce('offline')
      return
    }
    this.inFlight = true
    this.announce('saving')
    try {
      const result = await this.options.save(composition, this.revision)
      this.revision = result.currentRevision
      if (this.pending === composition) {
        this.pending = null
        this.store.forget(this.options.editId)
      }
      this.options.onSaved(result)
      this.announce('saved')
    } catch (error) {
      this.fail(error)
    } finally {
      this.inFlight = false
    }
    if (this.pending !== null && !this.blocked && this.state !== 'offline') {
      this.queue(this.pending)
    }
  }

  /**
   * Decide what one failed save means.
   *
   * A conflict is the only failure that stops autosaving: retrying it would fail the
   * same way forever, and the member is the only one who can say which document should
   * survive. Everything else is treated as a connection that will come back.
   */
  private fail(error: unknown): void {
    if (error instanceof ApiError && error.code === REVISION_CONFLICT_CODE) {
      this.blocked = true
      this.announce('conflict')
      this.options.onConflict(error.currentRevision)
      return
    }
    this.announce('offline')
  }

  private announce(status: SaveStatus): void {
    this.state = status
    this.options.onStatus(status)
  }
}
