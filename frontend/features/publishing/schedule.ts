/**
 * Turning what a member typed into the exact instant they meant.
 *
 * A wall-clock time is meaningless without the zone it was read in, so every schedule
 * carries both: the instant is what the backend stores, and the zone is what every later
 * view shows it in. Nothing here guesses a zone the member did not choose.
 */

const COMMON_ZONES = [
  'UTC',
  'Asia/Jakarta',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Europe/London',
  'Europe/Berlin',
  'America/New_York',
  'America/Los_Angeles',
]

/** The zone this browser is set to, which is only ever an opening suggestion. */
export function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

/** Every zone a member may pick, with the browser's own zone offered first. */
export function timezoneOptions(): string[] {
  const supported =
    typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : COMMON_ZONES
  const zones = supported.length > 0 ? supported : COMMON_ZONES
  const current = browserTimezone()
  return [current, ...zones.filter((zone) => zone !== current)]
}

/**
 * Read one `datetime-local` value as an instant in the named zone.
 *
 * The offset is resolved twice because the first pass is read at the wrong instant near a
 * daylight-saving boundary. Returns null where the value is not a complete local time.
 */
export function instantFor(localValue: string, timeZone: string): string | null {
  const naive = Date.parse(`${localValue}:00Z`)
  if (Number.isNaN(naive)) {
    return null
  }
  const firstPass = naive - zoneOffsetMs(naive, timeZone)
  const instant = naive - zoneOffsetMs(firstPass, timeZone)
  return new Date(instant).toISOString()
}

/** Render one instant as the day a member reading it in that zone would call it. */
export function formatDay(instant: string, timeZone: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone,
  }).format(new Date(instant))
}

/** Render one instant as the clock time it shows in that zone. */
export function formatTime(instant: string, timeZone: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone,
  }).format(new Date(instant))
}

/** Render one instant the way every timeline entry and schedule line states it. */
export function formatInstant(instant: string, timeZone: string): string {
  return `${formatDay(instant, timeZone)} at ${formatTime(instant, timeZone)} (${timeZone})`
}

function zoneOffsetMs(utcMs: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(new Date(utcMs))
  const read = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? '0')
  const hour = read('hour')
  return (
    Date.UTC(read('year'), read('month') - 1, read('day'), hour === 24 ? 0 : hour, read('minute'), read('second')) -
    utcMs
  )
}
