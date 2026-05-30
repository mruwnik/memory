import { describe, it, expect, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithUser, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { EmailPanel, defaultSyncFolders } from './EmailPanel'
import type { ImapFolder } from '@/hooks/useSources'

const imapAccount = {
  id: 1,
  name: 'Work IMAP',
  email_address: 'work@example.com',
  account_type: 'imap',
  imap_server: 'imap.example.com',
  imap_port: 993,
  username: 'work',
  use_ssl: true,
  smtp_server: null,
  smtp_port: null,
  google_account_id: null,
  folders: ['INBOX', 'Sent'],
  tags: ['job'],
  last_sync_at: null,
  sync_error: null,
  active: true,
  send_enabled: true,
  created_at: '',
  updated_at: '',
  project_id: null,
  sensitivity: 'basic',
}

const gmailAccount = { id: 5, name: 'Gmail Bob', email: 'bob@gmail.com', active: true, last_sync_at: null, sync_error: null, folders: [] }

const baseRoutes = (emailAccounts: unknown[] = []) => ({
  '/email-accounts': { json: emailAccounts },
  '/google-drive/accounts': { json: [gmailAccount] },
  '/mcp/projects_list_all': { json: { jsonrpc: '2.0', id: 1, result: { content: [{ text: JSON.stringify({ projects: [], count: 0 }) }] } } },
  __default: { json: [] },
})

// Form labels are plain <label> elements (not htmlFor-associated) sitting in a
// formGroup div alongside their control, so resolve the control via the label's
// parent rather than getByLabelText.
const field = (container: HTMLElement, labelText: string | RegExp): HTMLElement => {
  const label = within(container).getByText(labelText, { selector: 'label' })
  const group = label.parentElement as HTMLElement
  const control = group.querySelector('input, select, textarea')
  if (!control) throw new Error(`No control for label ${labelText}`)
  return control as HTMLElement
}

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('EmailPanel load states', () => {
  it('shows loading then the empty state when no accounts', async () => {
    mockFetchRoutes(baseRoutes([]))
    renderWithUser(<EmailPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No email accounts configured')).toBeInTheDocument())
  })

  it('renders an error state with retry when the list fetch fails', async () => {
    const fetchMock = mockFetchRoutes({ ...baseRoutes([]), '/email-accounts': { status: 500, json: {} } })
    const { user } = renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('Failed to fetch email accounts')).toBeInTheDocument())
    const callsBefore = fetchMock.mock.calls.length
    await user.click(screen.getByText('Retry'))
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore))
  })

  it('renders a populated account with imap server detail', async () => {
    mockFetchRoutes(baseRoutes([imapAccount]))
    renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('Work IMAP')).toBeInTheDocument())
    expect(screen.getByText('work@example.com')).toBeInTheDocument()
    expect(screen.getByText('Server: imap.example.com:993')).toBeInTheDocument()
    expect(screen.getByText('Folders: INBOX, Sent')).toBeInTheDocument()
  })

  it('shows a sync error banner when present', async () => {
    mockFetchRoutes(baseRoutes([{ ...imapAccount, sync_error: 'auth failed' }]))
    renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('auth failed')).toBeInTheDocument())
  })
})

describe('EmailPanel create flow', () => {
  it('submits an IMAP account create with the entered fields', async () => {
    const fetchMock = mockFetchRoutes(baseRoutes([]))
    const { user } = renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('No email accounts configured')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Add Account' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(field(dialog, 'Name'), 'My Mail')
    await user.type(field(dialog, 'Email Address'), 'me@example.com')
    await user.type(field(dialog, 'IMAP Server'), 'imap.host.com')
    await user.type(field(dialog, 'Username'), 'meuser')
    await user.type(field(dialog, /Password/), 'secret')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([url, init]) => String(url).endsWith('/email-accounts') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
    })
    const post = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith('/email-accounts') && (init as RequestInit)?.method === 'POST',
    )!
    const body = JSON.parse((post[1] as RequestInit).body as string)
    expect(body).toMatchObject({
      name: 'My Mail',
      email_address: 'me@example.com',
      account_type: 'imap',
      imap_server: 'imap.host.com',
      username: 'meuser',
      password: 'secret',
      use_ssl: true,
    })
  })

  it('surfaces the server error message inside the form on create failure', async () => {
    mockFetchRoutes({
      ...baseRoutes([]),
      '/email-accounts': { status: 400, json: { detail: 'duplicate account' } },
    })
    // first GET returns 400 -> panel error, so instead make GET ok but POST fail.
    const fetchMock = mockFetchRoutes({
      ...baseRoutes([]),
      __default: { json: [] },
    })
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input)
      const method = (init as RequestInit)?.method ?? 'GET'
      if (url.includes('/email-accounts') && method === 'POST') {
        return { ok: false, status: 400, json: async () => ({ detail: 'duplicate account' }), text: async () => '' } as unknown as Response
      }
      if (url.includes('/email-accounts')) return { ok: true, status: 200, json: async () => [], text: async () => '[]' } as unknown as Response
      if (url.includes('/google-drive/accounts')) return { ok: true, status: 200, json: async () => [gmailAccount], text: async () => '' } as unknown as Response
      return { ok: true, status: 200, headers: new Headers(), json: async () => ({ jsonrpc: '2.0', id: 1, result: { content: [{ text: JSON.stringify([[]]) }] } }), text: async () => JSON.stringify({ jsonrpc: '2.0', id: 1, result: { content: [{ text: JSON.stringify([[]]) }] } }) } as unknown as Response
    })
    const { user } = renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('No email accounts configured')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Account' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(field(dialog, 'Name'), 'Dup')
    await user.type(field(dialog, 'Email Address'), 'd@e.com')
    await user.type(field(dialog, 'IMAP Server'), 'imap.host.com')
    await user.type(field(dialog, 'Username'), 'u')
    await user.type(field(dialog, /Password/), 'p')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('duplicate account')).toBeInTheDocument()
  })

  it('disables Save for gmail when no google accounts are connected', async () => {
    const fetchMock = mockFetchRoutes({ ...baseRoutes([]), '/google-drive/accounts': { json: [] } })
    void fetchMock
    const { user } = renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('No email accounts configured')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Add Account' }))
    const dialog = await screen.findByRole('dialog')
    await user.selectOptions(field(dialog, 'Account Type'), 'gmail')
    expect(within(dialog).getByText(/No Google accounts connected/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})

describe('EmailPanel mutate flows', () => {
  it('toggles active via the status switch with a PATCH', async () => {
    const fetchMock = mockFetchRoutes(baseRoutes([imapAccount]))
    const { user } = renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('Work IMAP')).toBeInTheDocument())
    await user.click(screen.getByRole('switch'))
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes('/email-accounts/1') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ active: false })
    })
  })

  it('deletes after confirming the dialog', async () => {
    const fetchMock = mockFetchRoutes(baseRoutes([imapAccount]))
    const { user } = renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('Work IMAP')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => {
      const del = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes('/email-accounts/1') && (init as RequestInit)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
    })
  })

  it('does not delete when the confirm dialog is cancelled', async () => {
    const fetchMock = mockFetchRoutes(baseRoutes([imapAccount]))
    const { user } = renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('Work IMAP')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    const del = fetchMock.mock.calls.find(
      ([url, init]) => String(url).includes('/email-accounts/1') && (init as RequestInit)?.method === 'DELETE',
    )
    expect(del).toBeFalsy()
  })

  it('triggers a sync POST from the Sync button', async () => {
    const fetchMock = mockFetchRoutes(baseRoutes([imapAccount]))
    const { user } = renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('Work IMAP')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => {
      const sync = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes('/email-accounts/1/sync') && (init as RequestInit)?.method === 'POST',
      )
      expect(sync).toBeTruthy()
    })
  })
})

describe('EmailPanel edit flow', () => {
  it('opens edit with the account type field disabled and omits empty password', async () => {
    const fetchMock = mockFetchRoutes(baseRoutes([imapAccount]))
    const { user } = renderWithUser(<EmailPanel />)
    await waitFor(() => expect(screen.getByText('Work IMAP')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog')
    expect(field(dialog, 'Account Type')).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes('/email-accounts/1') && (init as RequestInit)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse((patch![1] as RequestInit).body as string)).not.toHaveProperty('password')
    })
  })
})

describe('defaultSyncFolders', () => {
  const folder = (name: string, flags: string[] = [], selectable = true): ImapFolder => ({
    name,
    flags,
    selectable,
  })

  it('selects INBOX (any case) and the \\Sent-flagged folder', () => {
    const folders = [
      folder('INBOX', ['\\HasNoChildren']),
      folder('Sent', ['\\HasNoChildren', '\\Sent']),
      folder('Archive', ['\\Archive']),
      folder('logs'),
    ]
    expect(defaultSyncFolders(folders)).toEqual(['INBOX', 'Sent'])
  })

  it('matches INBOX case-insensitively and ignores a non-selectable inbox', () => {
    expect(defaultSyncFolders([folder('inbox')])).toEqual(['inbox'])
    expect(defaultSyncFolders([folder('INBOX', [], false)])).toEqual([])
  })

  it('falls back to a name match when the \\Sent flag is absent', () => {
    expect(defaultSyncFolders([folder('Sent')])).toEqual(['Sent'])
    expect(defaultSyncFolders([folder('Sent Items')])).toEqual(['Sent Items'])
    expect(defaultSyncFolders([folder('sent mail')])).toEqual(['sent mail'])
  })

  it('does not name-match a non-selectable Sent folder', () => {
    expect(defaultSyncFolders([folder('Sent', [], false)])).toEqual([])
  })

  it('returns an empty list when nothing matches', () => {
    expect(defaultSyncFolders([folder('logs'), folder('Archive', ['\\Archive'])])).toEqual([])
  })
})
