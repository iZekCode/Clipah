'use client'

import { useEffect, useRef } from 'react'

/** One frame at the proxy's nominal rate; the proxy's own rate is not exposed. */
export const FRAME_MS = 1000 / 30

export interface EditorKeyHandlers {
  onPlayPause: () => void
  onUndo: () => void
  onRedo: () => void
  onSplit: () => void
  onDelete: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  onSave: () => void
  onStep: (deltaMs: number) => void
  onAddMarker: () => void
  onHelp: () => void
}

/**
 * The editor's keyboard shortcuts.
 *
 * They are deliberately inert while a member is typing: a caption is text, and an editor
 * that treats `z` inside a caption as an undo is an editor that eats words. They also stay
 * quiet while a dialog is open, and leave alone any key a focused control already handled.
 */
export function useEditorKeys(handlers: EditorKeyHandlers): void {
  const current = useRef(handlers)
  current.current = handlers

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.defaultPrevented || isTextEntry(event.target)) {
        return
      }
      if (document.querySelector('[role="dialog"]') !== null) {
        return
      }
      const modified = event.metaKey || event.ctrlKey
      if (modified && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) {
          current.current.onRedo()
        } else {
          current.current.onUndo()
        }
        return
      }
      if (modified && event.key.toLowerCase() === 's') {
        event.preventDefault()
        current.current.onSave()
        return
      }
      if (modified || event.altKey) {
        return
      }
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault()
        const direction = event.key === 'ArrowRight' ? 1 : -1
        current.current.onStep(direction * (event.shiftKey ? 1_000 : FRAME_MS))
        return
      }
      if (event.key === 'm' || event.key === 'M') {
        current.current.onAddMarker()
        return
      }
      if (event.key === '?') {
        current.current.onHelp()
        return
      }
      if (event.key === ' ') {
        event.preventDefault()
        current.current.onPlayPause()
        return
      }
      if (event.key.toLowerCase() === 's') {
        current.current.onSplit()
        return
      }
      if (event.key === 'Delete' || event.key === 'Backspace') {
        current.current.onDelete()
        return
      }
      if (event.key === '+' || event.key === '=') {
        current.current.onZoomIn()
        return
      }
      if (event.key === '-') {
        current.current.onZoomOut()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [])
}

/** Whether the event landed somewhere a member is writing. */
function isTextEntry(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  )
}
