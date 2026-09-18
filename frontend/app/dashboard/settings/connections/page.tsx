import { Section } from '@/components/page-header'
import { SocialConnections } from '@/features/connections/SocialConnections'
import { SettingsLayout } from '@/features/settings/SettingsNav'
import { YouTubeConnectionDialog } from '@/features/uploads/YouTubeConnectionDialog'

/** Settings › Connections: the Source and Social Accounts of this Workspace. */
export default function ConnectionsPage() {
  return (
    <SettingsLayout description="Connect the accounts Clipah publishes to and imports from. Each shows its permissions, health, and expiry.">
      <SocialConnections />
      <Section title="Import sources">
        <YouTubeConnectionDialog />
      </Section>
    </SettingsLayout>
  )
}
