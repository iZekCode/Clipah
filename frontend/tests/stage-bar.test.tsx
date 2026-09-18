import { render, screen, within } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import { StageBar, pipelineStageIndex } from '@/components/media/stage-bar'

function stages() {
  return within(screen.getByRole('list', { name: 'Processing stages' })).getAllByRole('listitem')
}

describe('StageBar', () => {
  test('names the four real stages and marks the one in progress', () => {
    render(<StageBar kind="transcribe" status="running" />)

    expect(stages().map((stage) => stage.textContent)).toEqual([
      'Uploading (done)',
      'Importing (done)',
      'Transcribing (in progress)',
      'Finding moments (not started)',
    ])
  })

  test('shows the upload percentage only while the browser is uploading', () => {
    render(<StageBar kind={null} status={null} uploadPercent={42} />)

    expect(stages()[0]).toHaveTextContent('Uploading 42% (in progress)')
  })

  test('a finished analysis completes every stage', () => {
    render(<StageBar kind="analyze" status="succeeded" />)

    expect(stages().every((stage) => stage.textContent?.endsWith('(done)'))).toBe(true)
  })

  test('says when live updates are reconnecting and hosts recovery actions', () => {
    render(
      <StageBar
        kind="ingest"
        status="running"
        reconnecting
        actions={<button type="button">Stop</button>}
      />,
    )

    expect(screen.getByText('Reconnecting to live updates…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
  })

  test('maps job kinds onto stages and leaves other work off the bar', () => {
    expect(pipelineStageIndex('source_import')).toBe(1)
    expect(pipelineStageIndex('ingest')).toBe(1)
    expect(pipelineStageIndex('transcribe')).toBe(2)
    expect(pipelineStageIndex('analyze')).toBe(3)
    expect(pipelineStageIndex('preview_media')).toBe(-1)
    expect(pipelineStageIndex(null)).toBe(-1)
  })
})
