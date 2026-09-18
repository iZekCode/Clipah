import { PageHeader } from '@/components/page-header'
import { LibrarySearch } from '@/features/search/GlobalSearch'
import type { SearchEntityType } from '@/lib/api/generated/model'

const TYPES: readonly SearchEntityType[] = ['project', 'transcript', 'clip', 'campaign_output']

/** Search: everything this Workspace has already made, by the words it was made of. */
export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; type?: string }>
}) {
  const { q, type } = await searchParams
  const defaultTypes = TYPES.filter((entry) => entry === type)
  return (
    <section className="space-y-2">
      <PageHeader
        title="Search"
        description="Find anything by what was said. Transcript results open the video at that moment."
      />
      <LibrarySearch
        key={`${q ?? ''}|${type ?? ''}`}
        initialQuestion={q ?? ''}
        defaultTypes={defaultTypes}
        heading="Search this workspace"
      />
    </section>
  )
}
