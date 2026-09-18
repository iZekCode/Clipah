'use client'

import { useEffect, useRef, useState, type RefObject } from 'react'

/**
 * Whether an element has been on screen yet. Once seen it stays "seen", so a long grid only
 * signs media for the cards somebody scrolled to, and never asks twice for the same card.
 */
export function useInView<T extends Element>(): [RefObject<T | null>, boolean] {
  const element = useRef<T | null>(null)
  const [seen, setSeen] = useState(false)

  useEffect(() => {
    const target = element.current
    if (target === null || seen) return
    if (typeof IntersectionObserver === 'undefined') {
      setSeen(true)
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true)
          observer.disconnect()
        }
      },
      { rootMargin: '200px' },
    )
    observer.observe(target)
    return () => observer.disconnect()
  }, [seen])

  return [element, seen]
}
