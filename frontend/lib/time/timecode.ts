const WITH_MINUTES = /^(\d+):([0-5]?\d)(?:\.(\d{1,3}))?$/
const SECONDS_ONLY = /^(\d+)(?:\.(\d{1,3}))?$/

/** `m:ss.cc` — the precision captions and trims are edited at. */
export function formatTimecode(ms: number): string {
  const centiseconds = Math.max(0, Math.round(ms / 10))
  const minutes = Math.floor(centiseconds / 6_000)
  const seconds = Math.floor(centiseconds / 100) % 60
  const hundredths = centiseconds % 100
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(hundredths).padStart(2, '0')}`
}

/** Read `m:ss.cc`, `m:ss`, `s.cc`, or `s` back to milliseconds, or nothing. */
export function parseTimecode(text: string): number | null {
  const value = text.trim()
  const withMinutes = WITH_MINUTES.exec(value)
  if (withMinutes !== null) {
    return (
      Number(withMinutes[1]) * 60_000 + Number(withMinutes[2]) * 1_000 + fraction(withMinutes[3])
    )
  }
  const secondsOnly = SECONDS_ONLY.exec(value)
  if (secondsOnly !== null) {
    return Number(secondsOnly[1]) * 1_000 + fraction(secondsOnly[2])
  }
  return null
}

/** `m:ss` for ruler ticks. */
export function formatRuler(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1_000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function fraction(digits: string | undefined): number {
  return digits === undefined ? 0 : Number(digits.padEnd(3, '0'))
}
