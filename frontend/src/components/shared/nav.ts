import type { Module } from '@/hooks/useModule'

// The app's sections, shared by the sidebar (AppShell) and the Dashboard tiles.
export interface NavItem { to: string; title: string; description: string; scope?: string; module?: Module }
export interface NavSection { title: string; items: NavItem[] }

export const NAV_SECTIONS: NavSection[] = [
  {
    title: 'Knowledge & content',
    items: [
      { to: '/search', title: 'Search', description: 'Search through your knowledge base' },
      { to: '/ingestion', title: 'Ingestion overview', description: 'Item totals, recent additions, indexing status and content types' },
      { to: '/sources', title: 'Sources', description: 'Connect email, chat, code, feeds, files and other sources' },
      { to: '/notes', title: 'Notes', description: 'Browse and view your notes' },
      { to: '/reports', title: 'Reports', description: 'View and create rich HTML and PDF reports' },
      { to: '/people', title: 'People', description: 'Track and manage people you know', scope: 'people' },
      { to: '/teams', title: 'Teams', description: 'Manage teams and their members', module: 'teams' },
      { to: '/projects', title: 'Projects', description: 'Track projects and milestones', module: 'teams' },
    ],
  },
  {
    title: 'Productivity',
    items: [
      { to: '/calendar', title: 'Calendar', description: 'View upcoming events from your calendars' },
      { to: '/tasks', title: 'Tasks', description: 'Manage your todos and tasks' },
      { to: '/polls', title: 'Polls', description: 'Schedule meetings with availability polls' },
    ],
  },
  {
    title: 'AI',
    items: [
      { to: '/agents', title: 'Agents', description: 'Define and run agents over your memory', scope: 'agents' },
      { to: '/keys', title: 'LLM keys', description: 'Your Anthropic and OpenAI keys pay for summaries, search steps and agents' },
    ],
  },
  {
    title: 'System operations',
    items: [
      { to: '/metrics', title: 'Metrics', description: 'System health, task performance, and API usage' },
      { to: '/usage', title: 'Usage', description: 'LLM cost and tokens by source, model, agent, key and user' },
      { to: '/jobs', title: 'Jobs', description: 'View background job status and retry failed jobs' },
      { to: '/scheduled-tasks', title: 'Scheduled tasks', description: 'Manage automated notifications and agent runs' },
      { to: '/celery', title: 'Celery tasks', description: 'Beat schedule status and ingestion overview', scope: 'admin' },
      { to: '/server-settings', title: 'Server settings', description: 'Voyage key, modules, MCP servers and search limits for this server', scope: 'admin' },
      { to: '/ssh-keys', title: 'SSH keys', description: 'Manage SSH identities for Notes' },
      { to: '/users', title: 'User management', description: 'Manage users, permissions, and API keys', scope: 'admin:users' },
    ],
  },
]

export const isVisible = (
  { scope, module }: Pick<NavItem, 'scope' | 'module'>,
  hasScope: (scope: string) => boolean,
  modules: ReadonlySet<string>,
) => (!scope || hasScope(scope)) && (!module || modules.has(module))

export const visibleSections = (hasScope: (scope: string) => boolean, modules: ReadonlySet<string>) =>
  NAV_SECTIONS
    .map(s => ({ ...s, items: s.items.filter(i => isVisible(i, hasScope, modules)) }))
    .filter(s => s.items.length)

/** The item for the current location: the longest matching path, where a query (`?tab=notes`) must also match. */
export const activeItem = (items: NavItem[], pathname: string, search: string) => {
  const params = new URLSearchParams(search)
  const matches = items.filter(i => {
    const [path, query] = i.to.split('?')
    const pathMatches = pathname === path || pathname.startsWith(path + '/')
    return pathMatches && [...new URLSearchParams(query)].every(([k, v]) => params.get(k) === v)
  })
  return matches.sort((a, b) => b.to.length - a.to.length)[0]
}
