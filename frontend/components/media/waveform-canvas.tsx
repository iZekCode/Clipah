'use client'

import { useEffect, useRef, useState } from 'react'

import { peakBuckets } from '@/lib/media/waveform'
import { cn } from '@/lib/utils'

const BAR_WIDTH = 2
const BAR_GAP = 1

/** Loudness bars for a time range, lime inside the selected range and muted elsewhere. */
export function WaveformCanvas({
  peaks,
  peaksPerSecond,
  startMs,
  endMs,
  selection = null,
  className,
}: {
  peaks: Uint8Array | null
  peaksPerSecond: number | null
  startMs: number
  endMs: number
  selection?: readonly [number, number] | null
  className?: string
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useEffect(() => {
    const element = canvas.current
    if (element === null) return
    const measure = () => setSize({ width: element.clientWidth, height: element.clientHeight })
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const element = canvas.current
    if (element === null || peaks === null || peaksPerSecond === null || size.width === 0) return
    const context = element.getContext('2d')
    if (context === null) return
    const ratio = window.devicePixelRatio || 1
    element.width = Math.round(size.width * ratio)
    element.height = Math.round(size.height * ratio)
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, size.width, size.height)
    const styles = getComputedStyle(element)
    const idle = `rgb(${styles.getPropertyValue('--muted-foreground').trim() || '161 161 168'})`
    const chosen = `rgb(${styles.getPropertyValue('--primary').trim() || '198 255 61'})`
    const buckets = Math.floor(size.width / (BAR_WIDTH + BAR_GAP))
    peakBuckets(peaks, peaksPerSecond, startMs, endMs, buckets).forEach((value, index) => {
      const atMs = startMs + ((index + 0.5) / buckets) * (endMs - startMs)
      const barHeight = Math.max(1, value * size.height)
      context.fillStyle =
        selection !== null && atMs >= selection[0] && atMs < selection[1] ? chosen : idle
      context.fillRect(
        index * (BAR_WIDTH + BAR_GAP),
        (size.height - barHeight) / 2,
        BAR_WIDTH,
        barHeight,
      )
    })
  }, [peaks, peaksPerSecond, startMs, endMs, selection, size])

  return <canvas ref={canvas} aria-hidden="true" className={cn('block h-full w-full', className)} />
}
