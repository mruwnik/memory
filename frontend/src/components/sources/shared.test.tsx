import { describe, it, expect, vi } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import { AccessSelects, ProjectField, ScheduleButton, SyncScheduleFields } from './shared'

const projects = [{ id: 3, title: 'Alpha', repo_path: 'o/alpha' }]

describe('ProjectField', () => {
  it.each([
    ['on', new Set(['teams']), 1],
    ['off', new Set<string>(), 0],
  ])('while teams is %s', (_state, modules, count) => {
    renderWithUser(<ProjectField hint="h" value={undefined} projects={projects} onChange={() => {}} />, { modules })
    expect(screen.queryAllByLabelText('Project')).toHaveLength(count)
  })
})

describe('AccessSelects', () => {
  it.each([
    ['on', new Set(['teams']), 1],
    ['off', new Set<string>(), 0],
  ])('keeps sensitivity and gates the project select while teams is %s', (_state, modules, count) => {
    renderWithUser(
      <AccessSelects projectId={undefined} sensitivity="basic" projects={projects} disabled={false} onChange={() => {}} />,
      { modules },
    )
    expect(screen.getByLabelText('Sensitivity')).toBeInTheDocument()
    expect(screen.queryAllByLabelText('Project')).toHaveLength(count)
  })
})

describe('SyncScheduleFields', () => {
  it.each([
    [{ check_interval: 60 }, ['Sync every']],
    [{ check_interval: 60, ingest_delay: 0 }, ['Sync every', 'Wait before ingesting']],
    [{ check_interval: 60, days_back: 30, max_items: 100 }, ['Sync every', 'Look back (days)', 'Max items per sync']],
  ])('shows only the knobs the source has', (value, labels) => {
    renderWithUser(<SyncScheduleFields value={value} onChange={() => {}} />)
    const shown = ['Sync every', 'Wait before ingesting', 'Look back (days)', 'Max items per sync']
      .filter(label => screen.queryByText(label) || screen.queryByLabelText(label))
    expect(shown).toEqual(labels)
  })

  it('keeps the other fields when one changes', async () => {
    const onChange = vi.fn()
    const { user } = renderWithUser(<SyncScheduleFields value={{ check_interval: 60, ingest_delay: 0, extra: 'x' }} onChange={onChange} />)
    const delayHours = screen.getAllByLabelText('Hours')[1]
    await user.clear(delayHours)
    await user.type(delayHours, '7')
    expect(onChange).toHaveBeenLastCalledWith({ check_interval: 60, ingest_delay: 7 * 60, extra: 'x' })
  })
})

describe('ScheduleButton', () => {
  it('saves the edited schedule and closes', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { user } = renderWithUser(<ScheduleButton schedule={{ check_interval: 60 }} onSave={onSave} />)
    await user.click(screen.getByRole('button', { name: 'Schedule' }))
    const hours = screen.getByLabelText('Hours')
    await user.clear(hours)
    await user.type(hours, '3')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({ check_interval: 180 })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
