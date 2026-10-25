import { describe, it, expect } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import { AccessSelects, ProjectField } from './shared'

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
