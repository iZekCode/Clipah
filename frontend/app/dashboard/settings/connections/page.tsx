import { YouTubeConnectionDialog } from '@/features/uploads/YouTubeConnectionDialog'

/** Connections: Source and Social Accounts of this Workspace */
export default function ConnectionsPage() {
  return (
    <section className="space-y-4">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Connections</h1>
        <p className="text-sm text-muted-foreground">
          Connected Source and Social Accounts, their scopes, health, and expiry appear here.
        </p>
      </div>
      <YouTubeConnectionDialog />
    </section>
  )
}
