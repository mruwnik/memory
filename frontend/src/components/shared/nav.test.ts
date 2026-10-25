import { ALL_MODULES } from '@/test/utils'
import { describe, it, expect } from 'vitest'
import { NAV_SECTIONS, activeItem, visibleSections } from './nav'

const items = NAV_SECTIONS.flatMap(s => s.items)

describe('activeItem', () => {
  it.each([
    ['/ui/agents', '', '/ui/agents'],
    ['/ui/agents/runs/12', '', '/ui/agents'],
    ['/ui/keys', '', '/ui/keys'],
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

const titlesFor = (hasScope: (scope: string) => boolean, modules: ReadonlySet<string>) =>
  visibleSections(hasScope, modules).flatMap(s => s.items.map(i => i.title))

describe('visibleSections', () => {
  it('hides scoped items the user lacks', () => {
    const titles = titlesFor(() => false, ALL_MODULES)
    expect(titles).not.toContain('Agents')
    expect(titles).not.toContain('User management')
    expect(titles).toContain('Search')
  })

  it('shows scoped items with the scope', () => {
    expect(titlesFor(s => s === 'agents', ALL_MODULES)).toContain('Agents')
  })

  it.each([
    ['Claude sessions', 'claude_containers'],
    ['Claude configurations', 'claude_containers'],
    ['Teams', 'teams'],
    ['Projects', 'teams'],
  ])('shows %s only while %s is on', (title, module) => {
    const others = new Set([...ALL_MODULES].filter(m => m !== module))
    expect(titlesFor(() => true, others)).not.toContain(title)
    expect(titlesFor(() => true, new Set([module]))).toContain(title)
  })

  it('needs the scope as well as the module', () => {
    expect(titlesFor(s => s !== 'claude_sessions', ALL_MODULES)).not.toContain('Claude sessions')
  })

  it('keeps core items with every module off', () => {
    expect(titlesFor(() => true, new Set())).toEqual(
      expect.arrayContaining(['Search', 'Agents', 'User management', 'Scheduled tasks']),
    )
  })
})
