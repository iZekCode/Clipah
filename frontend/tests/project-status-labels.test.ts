import { describe, expect, test } from 'vitest'

import { projectStatusLabel } from '@/features/projects/status-labels'

describe('project status labels', () => {
  test('a project with nothing on its way is waiting for a video', () => {
    expect(projectStatusLabel('created', 'upload')).toBe('Waiting for a video')
  })

  test('a video on its way says how it is arriving', () => {
    expect(projectStatusLabel('uploading', 'public_url')).toBe('Importing')
    expect(projectStatusLabel('uploading', 'upload')).toBe('Uploading')
  })

  test('preparing the video is not called importing', () => {
    expect(projectStatusLabel('ingesting', 'public_url')).toBe('Preparing video')
  })
})
