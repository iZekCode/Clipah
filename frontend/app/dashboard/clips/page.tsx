import { redirect } from 'next/navigation'

/** Clips live inside their Project now; the old address lands on the Projects list. */
export default function ClipsPage() {
  redirect('/dashboard/projects')
}
