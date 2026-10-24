import { describe, it, expect, vi } from 'vitest'
import { renderWithUser, screen, waitFor } from '@/test/utils'
import type { ExternalServer } from '@/hooks/useAgents'
import { ServerForm, parseHeaders, toAuth } from './ServerForm'

const saved = { id: 3, name: 'linear', url: 'https://mcp.linear.app/mcp', transport: 'http', auth_type: 'oauth' } as ExternalServer
const clean = { server: saved, authorize_url: null, secret_sent_to: null, error: null }

const fields = { token: 'tok', headers: 'X-Api-Key: k1\n\nX-Team: a:b', clientId: '', clientSecret: '', scopes: '' }

describe('ServerForm', () => {
  it('parses one header per line, keeping colons in values', () => {
    expect(parseHeaders(fields.headers)).toEqual({ 'X-Api-Key': 'k1', 'X-Team': 'a:b' })
  })

  it.each([
    ['bearer', fields, { type: 'headers', headers: { Authorization: 'Bearer tok' } }],
    ['headers', fields, { type: 'headers', headers: { 'X-Api-Key': 'k1', 'X-Team': 'a:b' } }],
    ['none', fields, { type: 'none' }],
    ['auto', fields, { type: 'auto' }],
    ['auto', { ...fields, clientId: 'cid', clientSecret: 's', scopes: 'read' }, { type: 'oauth', client_id: 'cid', client_secret: 's', scopes: 'read' }],
  ] as const)('turns %s into the auth the server expects', (choice, values, expected) => {
    expect(toAuth(choice, values)).toEqual(expected)
  })

  it('opens the authorization page and names where a secret goes', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    const onSubmit = vi.fn().mockResolvedValue({
      server: {}, authorize_url: 'https://auth.example.com/authorize?x=1', secret_sent_to: 'auth.example.com', error: null,
    })
    const { user } = renderWithUser(<ServerForm onSubmit={onSubmit} />)
    await user.type(screen.getByLabelText('URL'), 'https://mcp.example.com/mcp')
    await user.click(screen.getByRole('button', { name: 'Add server' }))

    await waitFor(() => expect(open).toHaveBeenCalledWith('https://auth.example.com/authorize?x=1', '_blank', 'noopener,noreferrer'))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ name: 'mcp.example.com', transport: 'http', auth: { type: 'auto' } }))
    expect(screen.getByText('auth.example.com')).toBeInTheDocument()
  })

  it('shows why a server could not connect', async () => {
    const onSubmit = vi.fn().mockResolvedValue({ server: {}, authorize_url: null, error: "couldn't reach an MCP server at that URL" })
    const { user } = renderWithUser(<ServerForm onSubmit={onSubmit} />)
    await user.type(screen.getByLabelText('URL'), 'http://10.0.0.5/mcp')
    await user.click(screen.getByRole('button', { name: 'Add server' }))
    await waitFor(() => expect(screen.getByText(/couldn't reach an MCP server/)).toBeInTheDocument())
  })

  it('keeps the saved credentials when an edit leaves auth alone', async () => {
    const onSubmit = vi.fn().mockResolvedValue(clean)
    const { user } = renderWithUser(<ServerForm server={saved} onSubmit={onSubmit} />)
    await user.clear(screen.getByLabelText('Name'))
    await user.type(screen.getByLabelText('Name'), 'renamed')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ name: 'renamed', url: saved.url, transport: 'http' }))
  })

  it('needs new credentials once the URL changes', async () => {
    const onSubmit = vi.fn().mockResolvedValue(clean)
    const { user } = renderWithUser(<ServerForm server={saved} onSubmit={onSubmit} />)
    await user.type(screen.getByLabelText('URL'), '/v2')
    await user.click(screen.getByRole('button', { name: 'Save and reconnect' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ auth: { type: 'auto' } })))
    expect(screen.queryByRole('option', { name: /Keep current credentials/ })).not.toBeInTheDocument()
  })
})
