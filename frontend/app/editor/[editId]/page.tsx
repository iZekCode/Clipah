'use client'

import { use } from 'react'

import { EditorScreen } from '@/features/editor/EditorScreen'

/**
 * The distraction-free editor route from Section 9.
 *
 * It sits outside `/dashboard` on purpose: the shell's navigation is not what a member
 * wants while they are cutting a clip. Everything the screen needs it asks the backend
 * for itself.
 */
export default function EditorPage({ params }: { params: Promise<{ editId: string }> }) {
  const { editId } = use(params)
  return <EditorScreen editId={editId} />
}
