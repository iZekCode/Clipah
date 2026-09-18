/**
 * The loudest peak in each of `buckets` equal slices of a time range, scaled to 0–1.
 *
 * Peaks are one byte per `1000 / peaksPerSecond` milliseconds, as the backend stores them.
 */
export function peakBuckets(
  peaks: Uint8Array,
  peaksPerSecond: number,
  startMs: number,
  endMs: number,
  buckets: number,
): number[] {
  if (buckets <= 0) return []
  const first = Math.max(0, Math.floor((startMs * peaksPerSecond) / 1000))
  const last = Math.min(peaks.length, Math.ceil((endMs * peaksPerSecond) / 1000))
  if (last <= first) return new Array<number>(buckets).fill(0)
  const span = (last - first) / buckets
  return Array.from({ length: buckets }, (_, bucket) => {
    const from = first + Math.floor(bucket * span)
    const to = Math.max(from + 1, first + Math.floor((bucket + 1) * span))
    let loudest = 0
    for (let position = from; position < to && position < last; position += 1) {
      loudest = Math.max(loudest, peaks[position] ?? 0)
    }
    return Math.round((loudest / 255) * 100) / 100
  })
}
