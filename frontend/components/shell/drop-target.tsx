'use client'

import { Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

/**
 * A full-window target for a video dragged in from the desktop.
 *
 * It only appears while files are being dragged, takes the first file, and hands over
 * only videos; anything else is refused where it was dropped.
 */
export function DropTarget({ onFile, onRefused }: { onFile: (file: File) => void; onRefused: () => void }) {
  const [active, setActive] = useState(false)
  const depth = useRef(0)
  const latest = useRef({ onFile, onRefused })
  latest.current = { onFile, onRefused }

  useEffect(() => {
    const carriesFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files')
    function onEnter(event: DragEvent): void {
      if (!carriesFiles(event)) return
      depth.current += 1
      setActive(true)
    }
    function onOver(event: DragEvent): void {
      if (carriesFiles(event)) event.preventDefault()
    }
    function onLeave(): void {
      depth.current = Math.max(0, depth.current - 1)
      if (depth.current === 0) setActive(false)
    }
    function onDrop(event: DragEvent): void {
      if (!carriesFiles(event)) return
      event.preventDefault()
      depth.current = 0
      setActive(false)
      const file = event.dataTransfer?.files[0]
      if (file === undefined) return
      if (file.type.startsWith('video/')) latest.current.onFile(file)
      else latest.current.onRefused()
    }
    window.addEventListener('dragenter', onEnter)
    window.addEventListener('dragover', onOver)
    window.addEventListener('dragleave', onLeave)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragenter', onEnter)
      window.removeEventListener('dragover', onOver)
      window.removeEventListener('dragleave', onLeave)
      window.removeEventListener('drop', onDrop)
    }
  }, [])

  if (!active) return null
  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-background/90 p-6">
      <div className="flex w-full max-w-3xl flex-col items-start gap-3 rounded-lg border-2 border-dashed border-primary p-10">
        <Upload aria-hidden="true" strokeWidth={1.75} className="size-8 text-primary" />
        <p className="font-display text-display">Drop to start a project</p>
        <p className="text-small text-muted-foreground">One long video: MP4, MOV, or WebM up to 2 GB.</p>
      </div>
    </div>
  )
}
