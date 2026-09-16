import { SettingsHeader } from '@/features/settings/SettingsNav'
import { TeamSettings } from '@/features/team/TeamSettings'

/** Settings › Members: roles, invitations, and ownership controls. */
export default function TeamPage() {
  return (
    <div className="space-y-6">
      <SettingsHeader description="Invite people, change what each member may do, and hand over ownership." />
      <TeamSettings />
    </div>
  )
}
