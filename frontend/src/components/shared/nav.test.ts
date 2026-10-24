import { describe, it, expect } from 'vitest'
import { NAV_SECTIONS, activeItem, visibleSections } from './nav'

const items = NAV_SECTIONS.flatMap(s => s.items)

describe('activeItem', () => {
  it.each([
    ['/ui/agents', '', '/ui/agents'],
    ['/ui/agents/runs/12', '', '/ui/agents'],
    ['/ui/agents/keys', '', '/ui/agents/keys'],
    ['/ui/sources', '', '/ui/sources'],
    ['/ui/sources', '?tab=email', '/ui/sources'],
    ['/ui/sources', '?tab=secrets', '/ui/sources?tab=secrets'],
    ['/ui/claude/abc', '', '/ui/claude'],
    ['/ui/settings', '', undefined],
    ['/ui/searching', '', undefined],
  ])('%s%s highlights %s', (pathname, search, expected) => {
    expect(activeItem(items, pathname, search)?.to).toBe(expected)
  })
})

describe('visibleSections', () => {
  it('hides scoped items the user lacks', () => {
    const titles = visibleSections(() => false).flatMap(s => s.items.map(i => i.title))
    expect(titles).not.toContain('Agents')
    expect(titles).not.toContain('User management')
    expect(titles).toContain('Search')
  })

  it('shows scoped items with the scope', () => {
    const titles = visibleSections(s => s === 'agents').flatMap(s => s.items.map(i => i.title))
    expect(titles).toContain('Agents')
  })
})
