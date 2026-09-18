import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { StylePanel } from '@/features/editor/StylePanel'
import { TEMPLATES } from '@/features/editor/templates'
import { WorkspaceProvider } from '@/features/workspaces/workspace-context'

import { renderWithApi, stubApi } from './support/api'
import { composition, currentUser, workspace } from './support/fixtures'

function renderStyle(onStyle = vi.fn(), onApplyTemplate = vi.fn()) {
  stubApi({
    'GET /api/v1/me': { body: currentUser() },
    'GET /api/v1/workspaces': { body: { workspaces: [workspace()] } },
  })
  renderWithApi(
    <WorkspaceProvider>
      <StylePanel
        composition={composition()}
        onStyle={onStyle}
        onApplyTemplate={onApplyTemplate}
        motion={<p>Motion body</p>}
      />
    </WorkspaceProvider>,
  )
  return { onStyle, onApplyTemplate }
}

describe('StylePanel', () => {
  test('templates are visual cards that draw their own look', async () => {
    const user = userEvent.setup()
    const { onApplyTemplate } = renderStyle()
    const template = TEMPLATES[0]!

    const templates = await screen.findByRole('region', { name: 'Templates' })
    const card = within(templates).getByRole('button', { name: new RegExp(template.name, 'i') })
    const slug = template.captionStyle.fontFamily.toLowerCase().replaceAll(' ', '-')
    expect(within(card).getByTestId('look-sample')).toHaveStyle({
      fontFamily: `var(--caption-font-${slug}), sans-serif`,
    })
    await user.click(card)

    expect(onApplyTemplate).toHaveBeenCalledWith(template)
  })

  test('fonts are named in their own faces', async () => {
    const user = userEvent.setup()
    const { onStyle } = renderStyle()

    const fonts = await screen.findByRole('radiogroup', { name: 'Caption font' })
    expect(within(fonts).getByRole('radio', { name: 'Montserrat' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(within(fonts).getByRole('radio', { name: 'Anton' })).toHaveStyle({
      fontFamily: 'var(--caption-font-anton), sans-serif',
    })
    await user.click(within(fonts).getByRole('radio', { name: 'Anton' }))

    expect(onStyle).toHaveBeenCalledWith({ fontFamily: 'Anton' })
  })

  test('weight, alignment, and decoration are segmented choices', async () => {
    const user = userEvent.setup()
    const { onStyle } = renderStyle()
    const style = await screen.findByRole('region', { name: 'Caption style' })

    await user.click(
      within(within(style).getByRole('group', { name: 'Caption weight' })).getByRole('button', {
        name: '800',
      }),
    )
    await user.click(
      within(within(style).getByRole('group', { name: 'Caption alignment' })).getByRole(
        'button',
        { name: 'Left' },
      ),
    )
    await user.click(
      within(within(style).getByRole('group', { name: 'Caption decoration' })).getByRole(
        'button',
        { name: 'Underline' },
      ),
    )

    expect(onStyle).toHaveBeenCalledWith({ weight: 800 })
    expect(onStyle).toHaveBeenCalledWith({ align: 'left' })
    expect(onStyle).toHaveBeenCalledWith({ decoration: 'underline' })
  })

  test('the motion tools sit at the end of the style panel', async () => {
    renderStyle()

    expect(await screen.findByText('Motion body')).toBeInTheDocument()
  })
})
