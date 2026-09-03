/**
 * The Server-Sent Events connection a component opened, under the test's control.
 *
 * jsdom ships no `EventSource`, and a real one would need a server. This stands in for
 * the browser's: a test drives the frames the backend would have written, and can see
 * whether the component closed the connection it no longer needs.
 */
export class FakeEventSource {
  static instances: FakeEventSource[] = []
  readonly url: string
  readonly withCredentials: boolean
  closed = false
  private readonly listeners = new Map<string, ((event: Event) => void)[]>()

  constructor(url: string, init?: EventSourceInit) {
    this.url = url
    this.withCredentials = init?.withCredentials ?? false
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  removeEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((existing) => existing !== listener),
    )
  }

  close(): void {
    this.closed = true
  }

  /** Deliver one Server-Sent Event exactly as the backend frames it. */
  emit(type: string, data: Record<string, unknown>, lastEventId = '1'): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data), lastEventId })
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event)
    }
  }

  /** Drop the connection the way a proxy or a sleeping laptop drops it. */
  fail(): void {
    for (const listener of this.listeners.get('error') ?? []) {
      listener(new Event('error'))
    }
  }
}
