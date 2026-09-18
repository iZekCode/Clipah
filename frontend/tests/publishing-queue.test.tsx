import { describe, expect, test } from 'vitest'

import { scheduleBucket } from '@/features/publishing/schedule'

describe('the scheduled timeline', () => {
  const now = new Date('2026-09-17T10:00:00+07:00')

  test('buckets by the calendar day in the zone the member chose', () => {
    expect(scheduleBucket('2026-09-17T22:00:00+07:00', now, 'Asia/Jakarta')).toBe('today')
    expect(scheduleBucket('2026-09-18T00:30:00+07:00', now, 'Asia/Jakarta')).toBe('tomorrow')
    expect(scheduleBucket('2026-09-19T09:00:00+07:00', now, 'Asia/Jakarta')).toBe('later')
  })

  test('the same instant can be tomorrow in one zone and today in another', () => {
    expect(scheduleBucket('2026-09-17T18:30:00+00:00', now, 'Asia/Jakarta')).toBe('tomorrow')
    expect(scheduleBucket('2026-09-17T18:30:00+00:00', now, 'UTC')).toBe('today')
  })

  test('tomorrow is the next calendar day even when the clocks change overnight', () => {
    // New York moves to daylight time at 02:00 on 8 March 2026; that night is 23 hours long.
    const lateEvening = new Date('2026-03-07T23:30:00-05:00')
    expect(scheduleBucket('2026-03-08T12:00:00-04:00', lateEvening, 'America/New_York')).toBe('tomorrow')
    expect(scheduleBucket('2026-03-09T00:30:00-04:00', lateEvening, 'America/New_York')).toBe('later')
  })
})
