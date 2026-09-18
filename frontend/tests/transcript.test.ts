import { describe, expect, test } from 'vitest'

import type { TranscriptWordResponse } from '@/lib/api/generated/model'
import {
  joinWords,
  overlaps,
  transcriptSegments,
  transcriptWindow,
} from '@/lib/media/transcript'

function word(
  id: number,
  text: string,
  startMs: number,
  punctuation = '',
  speaker = 'SPEAKER_00',
): TranscriptWordResponse {
  return { id: `w${id}`, text, punctuation, startMs, endMs: startMs + 400, speaker }
}

const words = [
  word(1, 'So', 0),
  word(2, 'here', 500, ','),
  word(3, 'it', 1_000),
  word(4, 'is', 1_500, '.'),
  word(5, 'Really', 2_000, '?', 'SPEAKER_01'),
  word(6, 'Yes', 12_500, '.', 'SPEAKER_00'),
  word(7, 'Later', 30_000),
]

describe('transcript text', () => {
  test('joins words with their own punctuation and nothing invented', () => {
    expect(joinWords(words.slice(0, 4))).toBe('So here, it is.')
  })

  test('breaks segments at sentence ends and at speaker changes', () => {
    expect(
      transcriptSegments(words).map((segment) => [
        segment.speaker,
        segment.text,
        segment.startMs,
        segment.endMs,
      ]),
    ).toEqual([
      ['SPEAKER_00', 'So here, it is.', 0, 1_900],
      ['SPEAKER_01', 'Really?', 2_000, 2_400],
      ['SPEAKER_00', 'Yes.', 12_500, 12_900],
      ['SPEAKER_00', 'Later', 30_000, 30_400],
    ])
  })

  test('a range overlaps a segment it touches on either side', () => {
    expect(overlaps({ startMs: 0, endMs: 1_900 }, 1_800, 5_000)).toBe(true)
    expect(overlaps({ startMs: 0, endMs: 1_900 }, 1_900, 5_000)).toBe(false)
  })

  test('a window splits what was said before, inside, and after a range', () => {
    expect(transcriptWindow(words, 1_000, 2_400, 11_000)).toEqual({
      before: 'So here,',
      inside: 'it is. Really?',
      after: 'Yes.',
    })
  })
})
