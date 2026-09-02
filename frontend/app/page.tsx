import Link from 'next/link'

const CAPABILITIES = [
  {
    title: 'Import once',
    description:
      'Bring in a YouTube video or an upload. The backend validates the source before a single frame is fetched.',
  },
  {
    title: 'Find the moments',
    description:
      'Transcription with real diarization feeds candidate extraction, deduplication, and ranking.',
  },
  {
    title: 'Review before you render',
    description:
      'Candidates arrive ranked with the evidence behind each one, so nothing renders on a guess.',
  },
] as const

/** The public landing page: what Clipah does, and the one way in. */
export default function LandingPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-10 px-6 py-16">
      <div className="space-y-4">
        <h1 className="text-4xl font-semibold tracking-tight">Clipah</h1>
        <p className="text-lg text-muted-foreground">
          Turn long-form video into ranked, review-ready short clips.
        </p>
        <Link
          href="/signin"
          className="inline-flex rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          Sign in
        </Link>
      </div>
      <ul className="grid gap-6 sm:grid-cols-3">
        {CAPABILITIES.map((capability) => (
          <li key={capability.title} className="space-y-2">
            <h2 className="text-sm font-medium">{capability.title}</h2>
            <p className="text-sm text-muted-foreground">{capability.description}</p>
          </li>
        ))}
      </ul>
    </main>
  )
}
