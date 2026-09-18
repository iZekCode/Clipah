import type { TranscriptWordResponse } from '@/lib/api/generated/model'

type Word = Pick<TranscriptWordResponse, 'text' | 'punctuation' | 'startMs' | 'endMs' | 'speaker'>

/** One run of speech that reads as a line: one speaker, up to a sentence end. */
export interface TranscriptSegment {
  startMs: number
  endMs: number
  speaker: string
  text: string
}

const SENTENCE_END = /[.!?…]["'”’)\]]*$/

/** Words as they were said, each with the punctuation transcription recorded. */
export function joinWords(words: readonly Word[]): string {
  return words.map((entry) => `${entry.text}${entry.punctuation}`).join(' ')
}

/** Group words into sentence-sized lines, breaking at a sentence end or a new speaker. */
export function transcriptSegments(words: readonly Word[]): TranscriptSegment[] {
  const segments: TranscriptSegment[] = []
  let current: Word[] = []
  const flush = () => {
    const first = current[0]
    const last = current.at(-1)
    if (first !== undefined && last !== undefined) {
      segments.push({
        startMs: first.startMs,
        endMs: last.endMs,
        speaker: first.speaker,
        text: joinWords(current),
      })
    }
    current = []
  }
  for (const entry of words) {
    if (current[0] !== undefined && current[0].speaker !== entry.speaker) flush()
    current.push(entry)
    if (SENTENCE_END.test(entry.punctuation)) flush()
  }
  flush()
  return segments
}

/** Whether a stretch of speech shares any time with a range. */
export function overlaps(
  segment: { startMs: number; endMs: number },
  startMs: number,
  endMs: number,
): boolean {
  return segment.startMs < endMs && segment.endMs > startMs
}

/** What was said shortly before, during, and shortly after a range. */
export function transcriptWindow(
  words: readonly Word[],
  startMs: number,
  endMs: number,
  contextMs = 10_000,
): { before: string; inside: string; after: string } {
  const inside = words.filter((entry) => overlaps(entry, startMs, endMs))
  const before = words.filter(
    (entry) => entry.endMs <= startMs && startMs - entry.startMs <= contextMs,
  )
  const after = words.filter((entry) => entry.startMs >= endMs && entry.endMs - endMs <= contextMs)
  return { before: joinWords(before), inside: joinWords(inside), after: joinWords(after) }
}
