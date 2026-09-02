import type { ReactNode } from 'react'

import { DashboardFrame } from '@/components/dashboard-frame'

/** Every `/dashboard` route renders inside one confirmed Session and one Workspace. */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <DashboardFrame>{children}</DashboardFrame>
}
