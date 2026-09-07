import { LibrarySearch } from '@/features/search/GlobalSearch'

/** Search: everything this Workspace has already made, by the words it was made of. */
export default function SearchPage() {
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Search</h1>
      <LibrarySearch />
    </section>
  )
}
