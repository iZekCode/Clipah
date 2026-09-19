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
      'Preparing video (done)',
      'Transcribing (in progress)',
      'Finding moments (not started)',
    ])
  })

  test('a YouTube project brings its video in by importing it', () => {
    render(<StageBar kind="source_import" status="running" sourceKind="public_url" />)

    expect(stages()[0]).toHaveTextContent('Importing (in progress)')
  })

  test("the project's own status places the bar before any job has reported", () => {
    render(<StageBar kind={null} status={null} projectStatus="ingesting" />)

    expect(stages().map((stage) => stage.textContent)).toEqual([
      'Uploading (done)',
      'Preparing video (in progress)',
      'Transcribing (not started)',
      'Finding moments (not started)',
    ])
  })

  test('a ready project shows every stage done', () => {
    render(<StageBar kind={null} status={null} projectStatus="ready" />)

    expect(stages().every((stage) => stage.textContent?.endsWith('(done)'))).toBe(true)
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
    expect(pipelineStageIndex('source_import')).toBe(0)
    expect(pipelineStageIndex('ingest')).toBe(1)
    expect(pipelineStageIndex('transcribe')).toBe(2)
    expect(pipelineStageIndex('analyze')).toBe(3)
    expect(pipelineStageIndex('preview_media')).toBe(-1)
    expect(pipelineStageIndex(null)).toBe(-1)
  })
})
