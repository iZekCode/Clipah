/** The Clipah wordmark in condensed display type, with a lime signal bar under the first letter. */
export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="font-display inline-flex items-end text-title leading-none tracking-tight">
      <span className="border-b-2 border-primary pb-0.5">C</span>
      {compact ? <span className="sr-only">lipah</span> : <span className="pb-0.5">LIPAH</span>}
    </span>
  )
}
