'use client'

import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'

const SHORTCUTS = [
  ['Space', 'Play or pause'],
  ['S', 'Split at the playhead'],
  ['Delete', 'Delete the selected item'],
  ['⌘Z', 'Undo'],
  ['⇧⌘Z', 'Redo'],
  ['⌘S', 'Save now'],
  ['← →', 'Step one frame'],
  ['⇧← ⇧→', 'Step one second'],
  ['M', 'Add a marker at the playhead'],
  ['+ −', 'Zoom the timeline'],
  ['?', 'Show these shortcuts'],
] as const

/** Every editor shortcut in one place. */
export function ShortcutSheet({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-sm" aria-describedby={undefined}>
        <SheetHeader>
          <SheetTitle>Editor shortcuts</SheetTitle>
        </SheetHeader>
        <dl className="mt-6 grid grid-cols-[5rem_minmax(0,1fr)] gap-x-4 gap-y-2.5">
          {SHORTCUTS.map(([key, action]) => (
            <div key={action} className="contents">
              <dt>
                <kbd className="rounded-sm border border-line-strong px-1.5 py-0.5 font-mono text-caption">
                  {key}
                </kbd>
              </dt>
              <dd className="text-small">{action}</dd>
            </div>
          ))}
        </dl>
      </SheetContent>
    </Sheet>
  )
}
