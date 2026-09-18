import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Undo2 } from 'lucide-react'
import { describe, expect, test, vi } from 'vitest'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { IconButton } from '@/components/ui/icon-button'
import { Radio } from '@/components/ui/radio'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Select } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'

describe('Signal primitives', () => {
  test('Select stays a native, labelled select that selectOptions can drive', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <label>
        Sort clips
        <Select defaultValue="rank" onChange={(event) => onChange(event.target.value)}>
          <option value="rank">Ranked</option>
          <option value="score">Highest score</option>
        </Select>
      </label>,
    )

    await user.selectOptions(screen.getByRole('combobox', { name: 'Sort clips' }), 'score')

    expect(onChange).toHaveBeenCalledWith('score')
  })

  test('Checkbox and Radio are native inputs with Signal skins', async () => {
    const user = userEvent.setup()
    render(
      <>
        <label>
          <Checkbox /> Snap to edges
        </label>
        <label>
          <Radio name="preset" value="portrait" /> Vertical
        </label>
      </>,
    )

    await user.click(screen.getByRole('checkbox', { name: 'Snap to edges' }))
    await user.click(screen.getByRole('radio', { name: 'Vertical' }))

    expect(screen.getByRole('checkbox', { name: 'Snap to edges' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Snap to edges' })).toHaveClass('signal-checkbox')
    expect(screen.getByRole('radio', { name: 'Vertical' })).toBeChecked()
  })

  test('Slider is a native range the keyboard can move', () => {
    render(<Slider aria-label="Volume" min={0} max={100} defaultValue={40} />)

    expect(screen.getByRole('slider', { name: 'Volume' })).toHaveValue('40')
  })

  test('SegmentedControl marks exactly one pressed option and reports a choice', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SegmentedControl
        label="Canvas shape"
        value="9:16"
        options={[
          { value: '9:16', label: '9:16' },
          { value: '1:1', label: '1:1' },
        ]}
        onChange={onChange}
      />,
    )

    expect(screen.getByRole('group', { name: 'Canvas shape' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '9:16' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '1:1' })).toHaveAttribute('aria-pressed', 'false')

    await user.click(screen.getByRole('button', { name: '1:1' }))

    expect(onChange).toHaveBeenCalledWith('1:1')
  })

  test('IconButton is named by its label and explains itself with its shortcut on focus', async () => {
    const user = userEvent.setup()
    render(<IconButton label="Undo" shortcut="⌘Z" icon={<Undo2 />} />)

    await user.tab()

    expect(screen.getByRole('button', { name: 'Undo' })).toHaveFocus()
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Undo⌘Z')
  })

  test('a loading Button keeps its label for width and says it is busy', () => {
    render(<Button loading>Export</Button>)

    const button = screen.getByRole('button', { name: /export/i })
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toBeDisabled()
  })
})
