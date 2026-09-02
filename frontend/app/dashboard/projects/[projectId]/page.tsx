import { ProjectDetail } from '@/features/projects/project-detail'

/** One Project: its source, its progress, and what it has produced so far. */
export default async function ProjectPage({
  params,
}: {
  params: Promise<{ projectId: string }>
}) {
  const { projectId } = await params
  return <ProjectDetail projectId={projectId} />
}
