// The app's sections, shared by the sidebar (AppShell) and the Dashboard tiles.
export interface NavItem { to: string; title: string; description: string; scope?: string }
export interface NavSection { title: string; items: NavItem[] }

export const NAV_SECTIONS: NavSection[] = [
  {
    title: 'Knowledge & content',
    items: [
      { to: '/ui/search', title: 'Search', description: 'Search through your knowledge base' },
      { to: '/ui/sources', title: 'Sources', description: 'Connect email, chat, code, feeds, files and other sources' },
      { to: '/ui/notes', title: 'Notes', description: 'Browse and view your notes' },
      { to: '/ui/reports', title: 'Reports', description: 'View and create rich HTML and PDF reports' },
      { to: '/ui/people', title: 'People', description: 'Track and manage people you know', scope: 'people' },
      { to: '/ui/teams', title: 'Teams', description: 'Manage teams and their members' },
      { to: '/ui/projects', title: 'Projects', description: 'Track projects and milestones' },
    ],
  },
  {
    title: 'Productivity',
    items: [
      { to: '/ui/calendar', title: 'Calendar', description: 'View upcoming events from your calendars' },
      { to: '/ui/tasks', title: 'Tasks', description: 'Manage your todos and tasks' },
      { to: '/ui/polls', title: 'Polls', description: 'Schedule meetings with availability polls' },
      { to: '/ui/check', title: 'Check questions', description: 'Submit questions to verify, research, or link, and see answers', scope: 'check' },
      { to: '/ui/agents', title: 'Agents', description: 'Define and run agents over your memory', scope: 'agents' },
    ],
  },
  {
    title: 'AI',
    items: [
      { to: '/ui/claude', title: 'Claude sessions', description: 'Spawn and manage Claude Code containers', scope: 'claude_sessions' },
      { to: '/ui/snapshots', title: 'Claude configurations', description: 'Manage Claude Code configurations' },
      { to: '/ui/keys', title: 'LLM keys', description: 'Anthropic keys pay for summaries and search; Anthropic and OpenAI keys pay for agents' },
    ],
  },
  {
    title: 'System operations',
    items: [
      { to: '/ui/metrics', title: 'Metrics', description: 'System health, task performance, and API usage' },
      { to: '/ui/telemetry', title: 'Telemetry', description: 'Claude Code usage, tokens, costs, and sessions' },
      { to: '/ui/jobs', title: 'Jobs', description: 'View background job status and retry failed jobs' },
      { to: '/ui/scheduled-tasks', title: 'Scheduled tasks', description: 'Manage automated notifications and Claude sessions' },
      { to: '/ui/celery', title: 'Celery tasks', description: 'Beat schedule status and ingestion overview', scope: 'admin' },
      { to: '/ui/sources?tab=secrets', title: 'Secrets', description: 'Manage encrypted tokens and other secrets' },
      { to: '/ui/users', title: 'User management', description: 'Manage users, permissions, and API keys', scope: 'admin:users' },
    ],
  },
]

export const visibleSections = (hasScope: (scope: string) => boolean) =>
  NAV_SECTIONS
    .map(s => ({ ...s, items: s.items.filter(i => !i.scope || hasScope(i.scope)) }))
    .filter(s => s.items.length)

/** The item for the current location: the longest matching path, where a query (`?tab=secrets`) must also match. */
export const activeItem = (items: NavItem[], pathname: string, search: string) => {
  const params = new URLSearchParams(search)
  const matches = items.filter(i => {
    const [path, query] = i.to.split('?')
    const pathMatches = pathname === path || pathname.startsWith(path + '/')
    return pathMatches && [...new URLSearchParams(query)].every(([k, v]) => params.get(k) === v)
  })
  return matches.sort((a, b) => b.to.length - a.to.length)[0]
}
