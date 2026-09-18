import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// Signal's named font sizes look like colours to tailwind-merge unless they are declared,
// and an undeclared `text-caption` would silently erase `text-muted-foreground`.
const merge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [
        { text: ['caption', 'small', 'body', 'title', 'h2', 'h1', 'display', 'hero'] },
      ],
    },
  },
})

/** Join class names, letting a later Tailwind class replace an earlier conflicting one. */
export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs))
}
