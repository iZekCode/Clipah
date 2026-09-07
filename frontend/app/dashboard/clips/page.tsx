import { LibrarySearch } from '@/features/search/GlobalSearch'

/** Clips: every Clip and variant of this Workspace */
export default function ClipsPage() {
  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Clips</h1>
        <p className="text-sm text-muted-foreground">
          Clips appear here once a Project has candidates you have accepted.
        </p>
      </div>
      <LibrarySearch
        defaultTypes={['clip']}
        heading="Find a clip"
        description="Search every clip this Workspace has produced by the words spoken in it, its topics, or its title."
      />
    </section>
  )
}
