import type { CompositionV1 } from '@/lib/api/generated/model'

type CaptionWord = CompositionV1['captions']['words'][number]

/**
 * Words grouped into the lines the export draws on the frame: at most five words, and a new
 * line after any pause longer than 400ms. The renderer groups them the same way, so the
 * preview shows the exact line a viewer will read.
 */
export function captionLines(
  words: readonly CaptionWord[],
): Array<{ startMs: number; endMs: number; words: CaptionWord[] }> {
  const lines: Array<{ startMs: number; endMs: number; words: CaptionWord[] }> = []
  let current: CaptionWord[] = []
  const flush = () => {
    const first = current[0]
    const last = current.at(-1)
    if (first !== undefined && last !== undefined) {
      lines.push({ startMs: first.startMs, endMs: last.endMs, words: current })
    }
    current = []
  }
  for (const word of words) {
    const last = current.at(-1)
    if (last !== undefined && (current.length >= 5 || word.startMs - last.endMs > 400)) {
      flush()
    }
    current.push(word)
  }
  flush()
  return lines
}

/** Words grouped the way a caption lane draws them: a few words, broken at pauses. */
export function captionPhrases(
  words: readonly CaptionWord[],
  { maxWords = 6, maxGapMs = 600 }: { maxWords?: number; maxGapMs?: number } = {},
): Array<{ id: string; startMs: number; endMs: number; text: string }> {
  const phrases: Array<{ id: string; startMs: number; endMs: number; text: string }> = []
  let current: CaptionWord[] = []
  const flush = () => {
    const first = current[0]
    const last = current.at(-1)
    if (first !== undefined && last !== undefined) {
      phrases.push({
        id: first.id,
        startMs: first.startMs,
        endMs: last.endMs,
        text: current.map((word) => word.text).join(' '),
      })
    }
    current = []
  }
  for (const word of words) {
    const last = current.at(-1)
    if (
      last !== undefined &&
      (word.startMs - last.endMs > maxGapMs || current.length >= maxWords)
    ) {
      flush()
    }
    current.push(word)
  }
  flush()
  return phrases
}
