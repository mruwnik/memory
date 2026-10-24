import { describe, it, expect, beforeEach } from 'vitest'
import type { ComponentType } from 'react'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithRouter, mockFetchRoutes, setAuthCookies, clearCookies, type MockResponseInit } from '@/test/utils'
import { EmailPanel } from './EmailPanel'
import { FeedsPanel } from './FeedsPanel'
import { CalendarPanel } from './CalendarPanel'
import { TranscriptsPanel } from './TranscriptsPanel'
import { AccountsPanel } from './AccountsPanel'

const mcp = (payload: unknown) => ({
  json: { jsonrpc: '2.0', id: 1, result: { content: [{ text: JSON.stringify(payload) }] } },
})

const llm_key = { choice: 'default', key_id: null, label: 'personal', provider: 'anthropic', paid_by_you: true, runs: true, can_choose_default: true }
const common = { llm_key, tags: [], active: true, last_sync_at: null, sync_error: null, created_at: '', updated_at: '', project_id: null, sensitivity: 'basic' }

const emailAccount = {
  ...common, id: 1, name: 'Work IMAP', email_address: 'work@example.com', account_type: 'imap', imap_server: 'imap.example.com',
  imap_port: 993, username: 'work', use_ssl: true, smtp_server: null, smtp_port: null, google_account_id: null, folders: [], send_enabled: true,
}
const feed = { ...common, id: 7, url: 'https://blog.example.com/feed.xml', title: 'Example Blog', description: null, check_interval: 1440, last_checked_at: null }
const calendarAccount = {
  ...common, id: 2, name: 'My CalDAV', calendar_type: 'caldav', caldav_url: 'https://dav.example.com/cal/', caldav_username: 'me',
  google_account_id: null, google_account: null, calendar_ids: [], check_interval: 15, sync_past_days: 30, sync_future_days: 90,
}
const transcriptAccount = { ...common, id: 3, name: 'My Fireflies', provider: 'fireflies', has_api_key: true, has_webhook_secret: false }
const githubAccount = {
  ...common, id: 4, name: 'My GH', verified_login: 'octocat', auth_type: 'pat', has_access_token: true, has_private_key: false,
  app_id: null, installation_id: null, repos: [],
}

const keys = mcp({ providers: { anthropic: [{ id: 7, label: 'work', key_hint: 'ab12', provider: 'anthropic' }], openai: [] } })

const panels: [string, ComponentType, string, Record<string, MockResponseInit>][] = [
  ['Email', EmailPanel, '/email-accounts/1', { '/email-accounts': { json: [emailAccount] }, '/google-drive/accounts': { json: [] } }],
  ['Feeds', FeedsPanel, '/article-feeds/7', { '/article-feeds': { json: [feed] } }],
  ['Calendar', CalendarPanel, '/calendar-accounts/2', {
    '/calendar-accounts': { json: [calendarAccount] }, '/google-drive/accounts': { json: [] }, '/mcp/organizer_upcoming': mcp([]),
  }],
  ['Transcripts', TranscriptsPanel, '/transcript-accounts/3', {
    '/transcript-accounts/providers': { json: ['fireflies'] }, '/transcript-accounts': { json: [transcriptAccount] },
  }],
  ['GitHub', AccountsPanel, '/github/accounts/4', {
    '/github/accounts': { json: [githubAccount] }, '/google-drive/accounts': { json: [] },
    '/google-drive/config': { status: 404, json: { detail: 'no config' } },
  }],
]

const openEdit = async (Panel: ComponentType, routes: Record<string, MockResponseInit>) => {
  const fetchMock = mockFetchRoutes({
    ...routes,
    '/mcp/agent_key_list': keys,
    '/mcp/projects_list_all': mcp({ projects: [], count: 0 }),
    '/auth/me': { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } },
    __default: { json: {} },
  })
  const { user } = renderWithRouter(<Panel />)
  await user.click(await screen.findByRole('button', { name: 'Edit' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByRole('option', { name: 'work (••••ab12)' })
  return { fetchMock, user, dialog }
}

const patchBody = (fetchMock: ReturnType<typeof mockFetchRoutes>, url: string) => {
  const patch = fetchMock.mock.calls.find(([u, init]) => String(u).includes(url) && (init as RequestInit)?.method === 'PATCH')
  return JSON.parse((patch![1] as RequestInit).body as string)
}

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('LLM key in source forms', () => {
  it.each(panels)('%s sends no llm_key when it is left alone', async (_name, Panel, url, routes) => {
    const { fetchMock, user, dialog } = await openEdit(Panel, routes)
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(patchBody(fetchMock, url)).not.toHaveProperty('llm_key'))
  })

  it.each(panels)('%s shows and sends the picked key', async (_name, Panel, url, routes) => {
    const { fetchMock, user, dialog } = await openEdit(Panel, routes)
    const select = within(dialog).getByLabelText('LLM key')
    await user.selectOptions(select, '7')
    expect(select).toHaveDisplayValue('work (••••ab12)')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(patchBody(fetchMock, url)).toMatchObject({ llm_key: 7 }))
  })
})
