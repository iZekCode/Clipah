import { LibrarySearch } from '@/features/search/GlobalSearch'

/** Assets: uploads, stock, and generated media with their provenance */
export default function AssetsPage() {
  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Assets</h1>
        <p className="text-sm text-muted-foreground">
          Uploaded, stock, and generated assets appear here with where each one came from.
        </p>
      </div>
      {/*
        Assets themselves are not in the library index: nothing about a stored file is
        text a person searches for. What a member is looking for on this screen is the
        moment a file belongs to, so the search here is scoped to transcripts and clips.
      */}
      <LibrarySearch
        defaultTypes={['transcript']}
        heading="Find the moment an asset belongs to"
        description="Search the spoken transcript of every Project in this Workspace, then open it at its timecode."
      />
    </section>
  )
}
