'use client'

import { useEffect, useRef } from 'react'

export interface ReviewKeyHandlers {
  onPlayPause: () => void
  onNext: () => void
  onPrevious: () => void
  onEdit: () => void
  onExit: () => void
  onHelp: () => void
}

/** Review mode's keyboard map; inert while a member is typing or a dialog is open. */
export function useReviewKeys(handlers: ReviewKeyHandlers): void {
  const latest = useRef(handlers)
  latest.current = handlers

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.metaKey || event.ctrlKey || event.altKey || typing(event.target)) return
      if (document.querySelector('[role="dialog"]') !== null) return
      const key = event.key
      // Enter on a focused button or link activates that control, not review's edit.
      if (key === 'Enter' && activatesControl(event.target)) return
      if (key === ' ') {
        event.preventDefault()
        latest.current.onPlayPause()
      } else if (key === 'j' || key === 'J') {
        latest.current.onNext()
      } else if (key === 'k' || key === 'K') {
        latest.current.onPrevious()
      } else if (key === 'e' || key === 'E' || key === 'Enter') {
        latest.current.onEdit()
      } else if (key === 'Escape') {
        latest.current.onExit()
      } else if (key === '?') {
        latest.current.onHelp()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
}

function typing(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement)
  )
}

function activatesControl(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.closest('button, a') !== null
}
