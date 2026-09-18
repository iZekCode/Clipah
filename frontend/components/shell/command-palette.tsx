'use client'

import { useQuery } from '@tanstack/react-query'
import { FilePlus2, Search } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { useWorkspaceScope } from '@/features/workspaces/workspace-context'
import type { ApiError } from '@/lib/api/client'
import type { SearchPageResponse } from '@/lib/api/generated/model'
import { searchApiV1SearchGet } from '@/lib/api/generated/search/search'

import { LIBRARY_NAVIGATION, PRIMARY_NAVIGATION, SETTINGS_ENTRY } from './navigation'

const DESTINATIONS = [...PRIMARY_NAVIGATION, ...LIBRARY_NAVIGATION, SETTINGS_ENTRY]
const SEARCH_DELAY_MS = 200

/**
 * Go anywhere, start anything, or find a moment by what was said — from the keyboard.
 *
 * Destinations filter locally; workspace results come from the same search the Search page
 * uses, asked only after typing pauses, and open at the deep link the backend returned.
 */
export function CommandPalette({
  open,
  onOpenChange,
  onNewProject,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onNewProject?: () => void
}) {
  const router = useRouter()
  const { active } = useWorkspaceScope()
  const [query, setQuery] = useState('')
  const question = useDebounced(query.trim(), SEARCH_DELAY_MS)

  const results = useQuery<SearchPageResponse, ApiError>({
    queryKey: ['/api/v1/search', active.id, 'palette', question],
    queryFn: ({ signal }) =>
      searchApiV1SearchGet({ q: question, workspace_id: active.id, limit: 8 }, { signal }),
    enabled: open && question.length >= 2,
    retry: false,
    staleTime: 30_000,
  })

  function go(href: string): void {
    onOpenChange(false)
    setQuery('')
    router.push(href)
  }

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput value={query} onValueChange={setQuery} placeholder="Search or jump to…" />
      <CommandList>
        <CommandEmpty>Nothing matches that yet.</CommandEmpty>
        <CommandGroup heading="Actions">
          <CommandItem
            value="New project"
            onSelect={() => {
              onOpenChange(false)
              onNewProject?.()
            }}
          >
            <FilePlus2 aria-hidden="true" strokeWidth={1.75} />
            New project
          </CommandItem>
        </CommandGroup>
        <CommandGroup heading="Go to">
          {DESTINATIONS.map((entry) => {
            const Icon = entry.icon
            return (
              <CommandItem key={entry.href} value={entry.label} onSelect={() => go(entry.href)}>
                <Icon aria-hidden="true" strokeWidth={1.75} />
                {entry.label}
              </CommandItem>
            )
          })}
        </CommandGroup>
        {results.data === undefined || results.data.results.length === 0 ? null : (
          <CommandGroup heading="In this workspace">
            {results.data.results.map((result) => (
              <CommandItem
                key={result.id}
                value={`${result.title} ${result.projectName} ${result.id}`}
                onSelect={() => go(result.deepLink)}
              >
                <Search aria-hidden="true" strokeWidth={1.75} />
                <span className="min-w-0 flex-1 truncate">{result.title}</span>
                <span className="truncate text-caption text-subtle-foreground">{result.projectName}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  )
}

/** Open the palette with ⌘K on macOS and Ctrl K elsewhere. */
export function useCommandPaletteShortcut(onOpen: () => void): void {
  const latest = useRef(onOpen)
  latest.current = onOpen
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        latest.current()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
}

function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])
  return settled
}
