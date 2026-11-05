import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithUser, mcpToolFromRequest, mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import { BeeperPanel } from './BeeperPanel'
import type { BeeperAccount, BeeperChat, BeeperNetwork, BeeperVerification } from '@/hooks/useBeeper'

const authMe = { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] }

const fresh: BeeperAccount = {
  id: 1,
  name: 'Personal',
  server_url: 'http://beeper-1:23373',
  email: null,
  beeper_user_id: null,
  signed_in: false,
  verified: false,
  setup_state: 'needs-login',
  step: 'email',
  collect_by_default: true,
  is_active: true,
  server_version: null,
  version_matches: true,
  connected: false,
  last_reconcile_at: null,
  last_event_at: null,
  created_at: null,
}

const ready: BeeperAccount = {
  ...fresh,
  email: 'me@example.com',
  beeper_user_id: '@me:beeper.com',
  signed_in: true,
  verified: true,
  setup_state: 'ready',
  step: 'done',
  server_version: '4.3.284',
}

const network: BeeperNetwork = {
  id: 10,
  account_id: 1,
  network_account_id: 'slack-1',
  network: 'slack',
  bridge_type: 'slack',
  bridge_provider: 'cloud',
  display_name: 'Work Slack',
  status: 'connected',
  collect_messages: true,
  project_id: null,
  sensitivity: 'basic',
  llm_key: { choice: 'default', key_id: null, label: 'personal', provider: 'anthropic', paid_by_you: true, runs: true, can_choose_default: true },
}

const chat: BeeperChat = {
  id: 100,
  network_id: 10,
  chat_id: '!room:beeper.com',
  title: 'general',
  chat_type: 'group',
  is_merged: false,
  merged_into: null,
  message_expiry_seconds: null,
  collect_messages: null,
  effective_collect: true,
  project_id: null,
  sensitivity: null,
  last_activity_at: null,
  backfill_done: false,
  backfill_stopped: false,
}

const verification = (state: string, actions: string[], emojis: string | null = null): BeeperVerification => ({
  id: 'verification-1',
  state,
  direction: 'outgoing',
  methods: ['sas'],
  available_actions: actions,
  other_device_name: 'Pixel',
  sas_emojis: emojis,
  sas_decimals: null,
  error_code: null,
})

const EMOJI = '🐶 🐱 🦁 🐎 🦄 🐷 🐘'

interface Fake {
  accounts: BeeperAccount[]
  networks: BeeperNetwork[]
  chats: BeeperChat[]
  verification: BeeperVerification | null
  registered: boolean
}

/** A stateful stand-in for the /beeper API, following the setup steps the real one takes. */
const fakeBeeper = (init: Partial<Fake> = {}) => {
  const fake: Fake = { accounts: [], networks: [], chats: [], verification: null, registered: true, ...init }
  const setAccount = (patch: Partial<BeeperAccount>) => {
    fake.accounts = fake.accounts.map(a => ({ ...a, ...patch }))
    return fake.accounts[0]
  }
  const setup = () => ({ account: fake.accounts[0], verification: fake.verification })
  const verified = () => {
    fake.verification = null
    setAccount({ verified: true, setup_state: 'ready', step: 'done' })
  }

  const respond = (method: string, path: string, body: Record<string, unknown> | undefined): [unknown, number?] => {
    switch (`${method} ${path}`) {
      case 'GET /servers':
        return [[{ url: 'http://beeper-0:23373', in_use: true }, { url: 'http://beeper-1:23373', in_use: false }]]
      case 'GET /accounts':
        return [fake.accounts]
      case 'POST /accounts':
        fake.accounts = [{ ...fresh, name: (body!.name as string) ?? null, server_url: body!.server_url as string, collect_by_default: body!.collect_by_default as boolean }]
        return [fake.accounts[0], 201]
      case 'GET /accounts/1':
        return [fake.accounts[0]]
      case 'POST /accounts/1/email':
        setAccount({ email: body!.email as string, step: 'code' })
        return [setup()]
      case 'POST /accounts/1/code':
        if (!fake.registered) {
          setAccount({ step: 'email' })
          return [{ detail: "There's no Beeper account for this email. Create one in the Beeper app first." }, 400]
        }
        setAccount({ signed_in: true, setup_state: 'needs-verification', step: 'verify' })
        return [setup()]
      case 'GET /accounts/1/setup':
        return [setup()]
      case 'POST /accounts/1/verifications':
        fake.verification = verification('ready', ['sas.start', 'cancel'])
        return [setup()]
      case 'POST /accounts/1/verifications/verification-1/sas-start':
        fake.verification = verification('sas_ready', ['sas.confirm', 'cancel'], EMOJI)
        return [setup()]
      case 'POST /accounts/1/verifications/verification-1/sas-confirm':
        verified()
        return [setup()]
      case 'POST /accounts/1/recovery-key':
        if (body!.recovery_key !== 'right key') return [{ detail: 'Beeper Server refused the request (invalid_recovery_key)' }, 400]
        verified()
        return [setup()]
      case 'POST /accounts/1/refresh':
      case 'GET /accounts/1/networks':
        return [fake.networks]
      case 'GET /networks/10/chats':
        return [fake.chats]
      case 'PATCH /chats/100':
        fake.chats = fake.chats.map(c => ({ ...c, ...body }))
        return [fake.chats[0]]
      case 'POST /accounts/1/sign-out':
        return [{ account: setAccount({ signed_in: false, verified: false, setup_state: 'needs-login', step: 'email' }), note: 'Ask the operator to wipe its data directory.' }]
    }
    return [{ detail: 'not found' }, 404]
  }

  const mock = mockFetch(async (input, init) => {
    const url = String(input)
    if (mcpToolFromRequest(input, init)) {
      return mockResponse({ json: { jsonrpc: '2.0', id: 1, result: { content: [{ text: JSON.stringify({ projects: [], count: 0 }) }] } } })
    }
    if (url.includes('/auth/me')) return mockResponse({ json: authMe })
    const path = url.slice(url.indexOf('/beeper') + '/beeper'.length)
    const body = init?.body ? JSON.parse(init.body as string) : undefined
    const [json, status = 200] = respond(init?.method ?? 'GET', path, body)
    return mockResponse({ status, json })
  })
  return { fake, mock }
}

// The writes the panel sent, as "METHOD /path" under /beeper.
const requests = (mock: ReturnType<typeof mockFetch>) => mock.mock.calls.flatMap(([input, init]) => {
  const url = String(input)
  const method = (init as RequestInit | undefined)?.method ?? 'GET'
  return method !== 'GET' && url.includes('/beeper/') ? [`${method} ${url.slice(url.indexOf('/beeper/') + '/beeper'.length)}`] : []
})

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

afterEach(() => {
  vi.restoreAllMocks()
})

const openSetup = async () => {
  const { user } = renderWithUser(<BeeperPanel />)
  await user.click(await screen.findByRole('button', { name: 'Sign in' }))
  return { user, dialog: await screen.findByRole('dialog') }
}

const signInToVerify = async (user: ReturnType<typeof renderWithUser>['user'], dialog: HTMLElement) => {
  await user.type(within(dialog).getByLabelText('Beeper account email'), 'me@example.com')
  await user.click(within(dialog).getByRole('button', { name: 'Send code' }))
  await user.type(await within(dialog).findByLabelText('Code'), '123456')
  await user.click(within(dialog).getByRole('button', { name: 'Sign in' }))
}

describe('BeeperPanel', () => {
  it('explains that only Beeper Cloud networks reach memory', async () => {
    fakeBeeper()
    renderWithUser(<BeeperPanel />)
    expect(await screen.findByText(/No Beeper accounts yet/)).toBeInTheDocument()
    expect(screen.getByText('Beeper Cloud')).toBeInTheDocument()
  })

  it('adds an account on a free Server, collecting everything by default', async () => {
    const { mock } = fakeBeeper()
    const { user } = renderWithUser(<BeeperPanel />)
    await user.click((await screen.findAllByRole('button', { name: 'Add Beeper account' }))[0])
    const dialog = await screen.findByRole('dialog')

    expect(await within(dialog).findByRole('option', { name: 'http://beeper-0:23373 (in use)' })).toBeDisabled()
    expect(within(dialog).getByLabelText('Collect everything')).toBeChecked()
    await user.type(within(dialog).getByLabelText('Name'), 'Personal')
    await user.click(within(dialog).getByRole('button', { name: 'Continue' }))

    expect(await within(dialog).findByLabelText('Beeper account email')).toBeInTheDocument()
    const post = mock.mock.calls.find(([url, init]) => String(url).endsWith('/beeper/accounts') && (init as RequestInit)?.method === 'POST')!
    expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({ server_url: 'http://beeper-1:23373', name: 'Personal', collect_by_default: true })
  })

  it('signs in, compares emoji and lists the networks', async () => {
    const { mock } = fakeBeeper({ accounts: [fresh], networks: [network] })
    const { user, dialog } = await openSetup()

    await signInToVerify(user, dialog)
    await user.click(await within(dialog).findByRole('button', { name: 'Start verification' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Show emoji' }))

    const emoji = await within(dialog).findByRole('list', { name: 'Verification emoji' })
    expect(within(emoji).getAllByRole('listitem').map(li => li.textContent)).toEqual(EMOJI.split(' '))
    expect(within(dialog).getByText(/Check these match the emoji in your Beeper app/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'They match' }))

    expect(await within(dialog).findByText(/Signed in and verified as me@example.com/)).toBeInTheDocument()
    expect(await within(dialog).findByText('Work Slack')).toBeInTheDocument()
    expect(requests(mock)).toEqual([
      'POST /accounts/1/email',
      'POST /accounts/1/code',
      'POST /accounts/1/verifications',
      'POST /accounts/1/verifications/verification-1/sas-start',
      'POST /accounts/1/verifications/verification-1/sas-confirm',
      'POST /accounts/1/refresh',
    ])
  })

  it('shows a verification the other device started, found by polling', async () => {
    fakeBeeper({
      accounts: [{ ...ready, verified: false, setup_state: 'needs-verification', step: 'verify' }],
      verification: verification('sas_ready', ['sas.confirm', 'cancel'], EMOJI),
    })
    const { user } = renderWithUser(<BeeperPanel />)
    await user.click(await screen.findByRole('button', { name: 'Verify device' }))
    expect(await screen.findByRole('list', { name: 'Verification emoji' })).toBeInTheDocument()
  })

  it('says when the email has no Beeper account and lets the user try another', async () => {
    fakeBeeper({ accounts: [fresh], registered: false })
    const { user, dialog } = await openSetup()

    await signInToVerify(user, dialog)

    expect(await within(dialog).findByText(/There's no Beeper account for this email/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Beeper account email')).toHaveValue('me@example.com')
    expect(within(dialog).getByRole('button', { name: 'Send code' })).toBeEnabled()
  })

  it('verifies with the recovery key, after a wrong one', async () => {
    fakeBeeper({ accounts: [fresh] })
    const { user, dialog } = await openSetup()
    await signInToVerify(user, dialog)

    await user.click(await within(dialog).findByRole('button', { name: 'Use recovery key instead' }))
    const key = within(dialog).getByLabelText('Recovery key')
    expect(key).toHaveAttribute('type', 'password')
    await user.type(key, 'wrong key')
    await user.click(within(dialog).getByRole('button', { name: 'Verify' }))
    expect(await within(dialog).findByText(/invalid_recovery_key/)).toBeInTheDocument()

    await user.clear(key)
    await user.type(key, 'right key')
    await user.click(within(dialog).getByRole('button', { name: 'Verify' }))
    expect(await within(dialog).findByText(/Signed in and verified/)).toBeInTheDocument()
  })

  it('shows account and network status', async () => {
    fakeBeeper({
      accounts: [{ ...ready, verified: false, setup_state: 'needs-verification', step: 'verify', version_matches: false, server_version: '4.4.0' }],
      networks: [{ ...network, status: 'backfilling' }, { ...network, id: 11, display_name: 'Old WhatsApp', network: 'whatsapp', status: 'removed' }],
    })
    renderWithUser(<BeeperPanel />)

    expect(await screen.findByText('Signed in, not verified')).toBeInTheDocument()
    expect(screen.getByText('needs-verification')).toBeInTheDocument()
    expect(screen.getByText(/The Beeper Server runs 4.4.0/)).toBeInTheDocument()
    expect(await screen.findByText('backfilling')).toBeInTheDocument()
    expect(screen.getByText('removed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verify device' })).toBeInTheDocument()
  })

  it.each([
    [true, 'Connected'],
    [false, 'Not connected'],
  ])('shows whether the collector is connected (%s)', async (connected, label) => {
    fakeBeeper({ accounts: [{ ...ready, connected }] })
    renderWithUser(<BeeperPanel />)
    expect(await screen.findByText(label)).toBeInTheDocument()
  })

  it('shows the sensitivity an inheriting chat gets from its network, and overrides it', async () => {
    const { mock } = fakeBeeper({
      accounts: [ready], networks: [{ ...network, sensitivity: 'confidential' }], chats: [{ ...chat, backfill_stopped: true }],
    })
    const { user } = renderWithUser(<BeeperPanel />)
    await user.click(await screen.findByRole('button', { name: 'Chats' }))

    expect(await screen.findByText('History incomplete')).toBeInTheDocument()
    const select = (await screen.findAllByRole('combobox', { name: 'Sensitivity' })).at(-1)!
    expect(within(select).getByRole('option', { selected: true })).toHaveTextContent('Inherit (Confidential)')
    await user.selectOptions(select, 'internal')
    await waitFor(() => expect(requests(mock)).toEqual(['PATCH /chats/100']))
    await user.selectOptions(select, 'inherit')

    await waitFor(() => expect(requests(mock)).toEqual(['PATCH /chats/100', 'PATCH /chats/100']))
    const bodies = mock.mock.calls.filter(([url]) => String(url).includes('/beeper/chats/100'))
      .map(([, init]) => JSON.parse((init as RequestInit).body as string))
    expect(bodies).toEqual([{ sensitivity: 'internal' }, { sensitivity: null }])
  })

  it.each([
    [true, 'Inherit (collect)'],
    [false, 'Inherit (skip)'],
  ])('shows what an inheriting chat gets from a network collecting=%s, and overrides it', async (collecting, label) => {
    const { mock } = fakeBeeper({ accounts: [ready], networks: [{ ...network, collect_messages: collecting }], chats: [chat] })
    const { user } = renderWithUser(<BeeperPanel />)
    await user.click(await screen.findByRole('button', { name: 'Chats' }))

    const select = await screen.findByRole('combobox', { name: 'Collect messages' })
    expect(within(select).getByRole('option', { selected: true })).toHaveTextContent(label)
    await user.selectOptions(select, 'skip')

    await waitFor(() => expect(requests(mock)).toEqual(['PATCH /chats/100']))
    const patch = mock.mock.calls.find(([url]) => String(url).includes('/beeper/chats/100'))!
    expect(JSON.parse((patch[1] as RequestInit).body as string)).toEqual({ collect_messages: false })
  })

  it('signs out after confirming and shows the operator note', async () => {
    fakeBeeper({ accounts: [ready] })
    const { user } = renderWithUser(<BeeperPanel />)
    await user.click(await screen.findByRole('button', { name: 'Sign out' }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Sign out' }))

    expect(await screen.findByText(/wipe its data directory/)).toBeInTheDocument()
    expect(await screen.findByText('Signed out')).toBeInTheDocument()
  })
})
