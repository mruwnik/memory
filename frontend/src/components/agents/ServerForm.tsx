import { useState } from 'react'
import type { ExternalServer, ServerAuth, ServerTransport, ServerUpsertResult } from '@/hooks/useAgents'
import { Button, Input, Label, Notice, Select, Textarea } from '@/components/shared/ui'


type AuthChoice = 'auto' | 'bearer' | 'headers' | 'none'

export interface ServerFormValues {
  name: string
  url: string
  transport: ServerTransport
  auth: ServerAuth
}

/** "Name: value" lines to a header map; blank lines are skipped. */
export const parseHeaders = (text: string): Record<string, string> =>
  Object.fromEntries(
    text
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean)
      .map(line => {
        const at = line.indexOf(':')
        return at < 0 ? [line, ''] : [line.slice(0, at).trim(), line.slice(at + 1).trim()]
      }),
  )

const hostOf = (url: string) => {
  try {
    return new URL(url).hostname
  } catch {
    return ''
  }
}

export function toAuth(choice: AuthChoice, fields: { token: string; headers: string; clientId: string; clientSecret: string; scopes: string }): ServerAuth {
  if (choice === 'bearer') return { type: 'headers', headers: { Authorization: `Bearer ${fields.token}` } }
  if (choice === 'headers') return { type: 'headers', headers: parseHeaders(fields.headers) }
  if (choice === 'none') return { type: 'none' }
  return {
    type: fields.clientId ? 'oauth' : 'auto',
    ...(fields.clientId ? { client_id: fields.clientId } : {}),
    ...(fields.clientSecret ? { client_secret: fields.clientSecret } : {}),
    ...(fields.scopes ? { scopes: fields.scopes } : {}),
  }
}

export function ServerForm({ server, onSubmit }: {
  server?: ExternalServer
  onSubmit: (values: ServerFormValues) => Promise<ServerUpsertResult>
}) {
  const [url, setUrl] = useState(server?.url ?? '')
  const [name, setName] = useState(server?.name ?? '')
  const [transport, setTransport] = useState<ServerTransport>(server?.transport ?? 'http')
  const [choice, setChoice] = useState<AuthChoice>('auto')
  const [token, setToken] = useState('')
  const [headers, setHeaders] = useState('')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [scopes, setScopes] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ServerUpsertResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const done = await onSubmit({
        name: name.trim() || hostOf(url),
        url: url.trim(),
        transport,
        auth: toAuth(choice, { token, headers, clientId, clientSecret, scopes }),
      })
      setResult(done)
      if (done.authorize_url) window.open(done.authorize_url, '_blank', 'noopener,noreferrer')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="space-y-3" onSubmit={submit}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <Label className="mb-1" htmlFor="server-url">URL</Label>
          <Input id="server-url" required placeholder="https://mcp.example.com/mcp"
            value={url} onChange={e => setUrl(e.target.value)} />
        </div>
        <div>
          <Label className="mb-1" htmlFor="server-name">Name</Label>
          <Input id="server-name" maxLength={100} placeholder={hostOf(url) || 'defaults to the host'}
            value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div>
          <Label className="mb-1" htmlFor="server-transport">Transport</Label>
          <Select id="server-transport" value={transport}
            onChange={e => setTransport(e.target.value as ServerTransport)}>
            <option value="http">Streamable HTTP</option>
            <option value="sse">SSE (legacy)</option>
          </Select>
        </div>
        <div>
          <Label className="mb-1" htmlFor="server-auth">Authentication</Label>
          <Select id="server-auth" value={choice} onChange={e => setChoice(e.target.value as AuthChoice)}>
            <option value="auto">Auto-detect (OAuth if the server asks)</option>
            <option value="bearer">Bearer token</option>
            <option value="headers">Custom headers</option>
            <option value="none">None</option>
          </Select>
        </div>
      </div>
      {choice === 'bearer' && (
        <div>
          <Label className="mb-1" htmlFor="server-token">Bearer token</Label>
          <Input id="server-token" type="password" required value={token} onChange={e => setToken(e.target.value)} />
        </div>
      )}
      {choice === 'headers' && (
        <div>
          <Label className="mb-1" htmlFor="server-headers">Headers, one "Name: value" per line</Label>
          <Textarea id="server-headers" className="h-20 font-mono" value={headers} onChange={e => setHeaders(e.target.value)} />
        </div>
      )}
      {choice === 'auto' && (
        <details className="text-sm">
          <summary className="cursor-pointer text-slate-500">Advanced: your own OAuth client</summary>
          <p className="text-xs text-slate-500 mt-2">
            Only for servers without dynamic client registration. Register an OAuth app with redirect URI{' '}
            <code>{`${window.location.origin}/auth/mcp/callback`}</code>.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-2">
            <div>
              <Label className="mb-1" htmlFor="server-client-id">Client ID</Label>
              <Input id="server-client-id" value={clientId} onChange={e => setClientId(e.target.value)} />
            </div>
            <div>
              <Label className="mb-1" htmlFor="server-client-secret">Client secret</Label>
              <Input id="server-client-secret" type="password" value={clientSecret} onChange={e => setClientSecret(e.target.value)} />
            </div>
            <div>
              <Label className="mb-1" htmlFor="server-scopes">Scopes</Label>
              <Input id="server-scopes" placeholder="as the server suggests" value={scopes} onChange={e => setScopes(e.target.value)} />
            </div>
          </div>
        </details>
      )}
      <Button variant="primary" type="submit" disabled={busy || !url.trim()}>
        {busy ? 'Connecting...' : server ? 'Save and reconnect' : 'Add server'}
      </Button>
      {error && <Notice tone="error">{error}</Notice>}
      {result?.error && <Notice tone="error">{result.error}</Notice>}
      {result?.authorize_url && (
        <Notice tone="info">
          Finish connecting in the tab that just opened, or <a className="underline" href={result.authorize_url} target="_blank" rel="noopener noreferrer">open the authorization page</a>.
        </Notice>
      )}
      {result?.secret_sent_to && (
        <Notice tone="warning">
          Your client secret will be sent to <strong>{result.secret_sent_to}</strong>, the server's token endpoint.
        </Notice>
      )}
    </form>
  )
}
