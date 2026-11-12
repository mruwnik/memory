import { describe, it, expect, beforeEach } from 'vitest'
import { renderWithRouter, screen, within, mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import IdentityCard from './IdentityCard'
import type { Identity } from '@/hooks/useIdentity'

const identity = (over: Partial<Identity> = {}): Identity => ({
  person: { id: 3, identifier: 'ada', display_name: 'Ada Lovelace' },
  accounts: [
    { kind: 'slack', id: 'T07ACME:U07AAA', label: 'ada (Acme)', verified: true, removable: false, on_person: true },
    { kind: 'github', id: 'ada-l', label: 'ada-l', verified: false, removable: true, on_person: true },
  ],
  suggestions: [],
  notify: {
    order: ['discord', 'slack', 'email'],
    chosen: 'slack',
    problems: { discord: 'no Discord account is linked: use Link a Discord account in Settings → Your identity', email: 'no email account you can send from' },
  },
  ...over,
})

type Handler = (init?: RequestInit) => Response
const serve = (routes: Record<string, Handler>, scopes = ['people']) =>
  mockFetch(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return mockResponse({ json: { user_id: 1, name: 'Ada', scopes } })
    const key = `${init?.method ?? 'GET'} ${url.replace(/^.*\/auth\/me\/identity/, '')}`
    const handler = routes[key]
    return handler ? handler(init) : mockResponse({ status: 404, json: { detail: `unrouted ${key}` } })
  })

const json = (body: unknown, status = 200): Handler => () => mockResponse({ status, json: body })
const group = (name: string) => screen.getByRole('heading', { name }).parentElement!.parentElement as HTMLElement

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('IdentityCard', () => {
  it('shows the person, accounts by kind, and where alerts go', async () => {
    serve({ 'GET ': json(identity()) })
    renderWithRouter(<IdentityCard />)

    expect(await screen.findByRole('link', { name: 'Ada Lovelace (ada)' })).toHaveAttribute('href', '/people?search=ada')
    expect(within(group('Slack')).getByText('verified')).toBeInTheDocument()
    expect(within(group('Slack')).queryByRole('button', { name: 'Unlink' })).not.toBeInTheDocument()
    expect(within(group('GitHub')).getByRole('button', { name: 'Unlink' })).toBeInTheDocument()
    expect(within(group('Email')).getByText('No Email account linked.')).toBeInTheDocument()

    const alerts = group('Where alerts go')
    expect(alerts).toHaveTextContent('Alerts try Discord DM, else Slack DM, else email.')
    expect(alerts).toHaveTextContent('Right now they go by Slack DM')
    expect(alerts).toHaveTextContent('no Discord account is linked: use Link a Discord account in Settings → Your identity.')
    expect(within(alerts).getByRole('link', { name: 'Add an email account' })).toHaveAttribute('href', '/sources?tab=email')
  })

  it('shows the person without a link when the user cannot open People', async () => {
    serve({ 'GET ': json(identity()) }, [])
    renderWithRouter(<IdentityCard />)
    expect(await screen.findByText('Ada Lovelace (ada)')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Ada Lovelace (ada)' })).not.toBeInTheDocument()
  })

  it('unlinks an account after confirming', async () => {
    const fetchMock = serve({
      'GET ': json(identity()),
      'DELETE /accounts/github/ada-l': json(identity({ accounts: identity().accounts.slice(0, 1) })),
    })
    const { user } = renderWithRouter(<IdentityCard />)
    await user.click(await within(await screen.findByRole('region')).findByRole('button', { name: 'Unlink' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Unlink' }))

    expect(await screen.findByText('Unlinked ada-l')).toBeInTheDocument()
    expect(within(group('GitHub')).getByText('No GitHub account linked.')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith('/accounts/github/ada-l') && i?.method === 'DELETE')).toBe(true)
  })

  it.each([
    [identity().person, 'Merge', "can't be undone"],
    [null, 'Claim', 'Its accounts become yours'],
  ])('claims a suggested record (own person %o) with %s', async (person, verb, warning) => {
    const suggested = { person: { id: 9, identifier: 'daniel', display_name: 'daniel' }, reasons: [{ kind: 'slack' as const, value: 'T07ACME:U07XYZ' }] }
    const fetchMock = serve({
      'GET ': json(identity({ person, suggestions: [suggested] })),
      'POST /claim': json(identity()),
    })
    const { user } = renderWithRouter(<IdentityCard />)

    expect(await screen.findByText('has your Slack account U07XYZ')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: verb }))
    expect(screen.getByRole('dialog')).toHaveTextContent(warning)
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: verb }))

    expect(await screen.findByRole('link', { name: 'Ada Lovelace (ada)' })).toBeInTheDocument()
    const claim = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/claim'))
    expect(claim?.[1]?.body).toBe('{"person_id":9}')
  })

  it('shows a Discord link code and clears it once the account is linked', async () => {
    const linked = identity({ accounts: [...identity().accounts, { kind: 'discord', id: '42', label: 'ada#1', verified: true, removable: true, on_person: false }] })
    const reads = [identity(), identity(), linked]
    serve({
      'GET ': () => mockResponse({ json: reads.shift() }),
      'POST /discord-code': json({ code: '48213907', expires_at: new Date(Date.now() + 15 * 60000).toISOString(), bots: ['MemoryBot'] }),
    })
    const { user } = renderWithRouter(<IdentityCard />)

    await user.click(await screen.findByRole('button', { name: 'Link a Discord account' }))
    expect(await screen.findByText(/to MemoryBot within 15 minutes/)).toHaveTextContent('DM link 48213907 to MemoryBot')

    await user.click(screen.getByRole('button', { name: "I've sent it: check again" }))
    expect(await screen.findByText(/No Discord account has been verified yet/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: "I've sent it: check again" }))
    expect(await within(group('Discord')).findByText('ada#1')).toBeInTheDocument()
    expect(within(group('Discord')).getByText('On another person record: ask an admin to merge')).toBeInTheDocument()
    expect(screen.queryByText(/48213907/)).not.toBeInTheDocument()
  })

  it('names a GitHub reason by the login of the verified account it comes from', async () => {
    const accounts = [{ kind: 'github' as const, id: '583231', label: 'octo', verified: true, removable: false, on_person: false }]
    const suggested = { person: { id: 9, identifier: 'daniel', display_name: 'daniel' }, reasons: [{ kind: 'github' as const, value: '583231' }] }
    serve({ 'GET ': json(identity({ accounts, suggestions: [suggested] })) })
    renderWithRouter(<IdentityCard />)

    expect(await screen.findByText('has your GitHub account octo')).toBeInTheDocument()
  })

  it('shows a load failure inside the card with a retry', async () => {
    serve({ 'GET ': json({ detail: 'Identity service unavailable' }, 503) })
    renderWithRouter(<IdentityCard />)
    expect(await screen.findByText('Identity service unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
