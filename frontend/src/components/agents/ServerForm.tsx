import { useState } from 'react'
import type { ExternalServer, ServerAuth, ServerTransport, ServerUpsertResult } from '@/hooks/useAgents'
import { Button, Field, FormActions, Hint, Input, Notice, Select, Textarea, useSubmit } from '@/components/shared/ui'

type AuthChoice = 'keep' | 'auto' | 'bearer' | 'headers' | 'none'

export const AUTH_LABELS: Record<ExternalServer['auth_type'], string> = { oauth: 'OAuth', headers: 'Headers', none: 'No auth' }

export interface ServerFormValues {
  name: string
  url: string
  transport: ServerTransport
  /** Omitted to keep the saved credentials. */
  auth?: ServerAuth
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

export function toAuth(choice: Exclude<AuthChoice, 'keep'>, fields: { token: string; headers: string; clientId: string; clientSecret: string; scopes: string }): ServerAuth {
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
  const [picked, setPicked] = useState<AuthChoice>(server ? 'keep' : 'auto')
  const [token, setToken] = useState('')
  const [headers, setHeaders] = useState('')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [scopes, setScopes] = useState('')
  const [result, setResult] = useState<ServerUpsertResult | null>(null)
  const { busy, error, submit } = useSubmit()

  // Credentials were issued for one URL and transport; changing either needs new ones.
  const canKeep = !!server && url.trim() === server.url && transport === server.transport
  const choice = picked === 'keep' && !canKeep ? 'auto' : picked

  const save = (e: React.FormEvent) => {
    e.preventDefault()
    submit(async () => {
      const done = await onSubmit({
        name: name.trim() || hostOf(url),
        url: url.trim(),
        transport,
        ...(choice === 'keep' ? {} : { auth: toAuth(choice, { token, headers, clientId, clientSecret, scopes }) }),
      })
      setResult(done)
      if (done.authorize_url) window.open(done.authorize_url, '_blank', 'noopener,noreferrer')
    })
  }

  return (
    <form className="space-y-3" onSubmit={save}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="URL">
          <Input required placeholder="https://mcp.example.com/mcp" value={url} onChange={e => setUrl(e.target.value)} />
        </Field>
        <Field label="Name">
          <Input maxLength={100} placeholder={hostOf(url) || 'defaults to the host'} value={name} onChange={e => setName(e.target.value)} />
        </Field>
        <Field label="Transport">
          <Select value={transport} onChange={e => setTransport(e.target.value as ServerTransport)}>
            <option value="http">Streamable HTTP</option>
            <option value="sse">SSE (legacy)</option>
          </Select>
        </Field>
        <Field label="Authentication"
          hint={server && !canKeep ? 'A new URL or transport needs new credentials.' : undefined}>
          <Select value={choice} onChange={e => setPicked(e.target.value as AuthChoice)}>
            {server && canKeep && <option value="keep">Keep current credentials ({AUTH_LABELS[server.auth_type]})</option>}
            <option value="auto">Auto-detect (OAuth if the server asks)</option>
            <option value="bearer">Bearer token</option>
            <option value="headers">Custom headers</option>
            <option value="none">None</option>
          </Select>
        </Field>
      </div>
      {choice === 'bearer' && (
        <Field label="Bearer token">
          <Input type="password" required value={token} onChange={e => setToken(e.target.value)} />
        </Field>
      )}
      {choice === 'headers' && (
        <Field label='Headers, one "Name: value" per line'>
          <Textarea className="h-20 font-mono" value={headers} onChange={e => setHeaders(e.target.value)} />
        </Field>
      )}
      {choice === 'auto' && (
        <details className="text-sm">
          <summary className="cursor-pointer text-slate-500">Advanced: your own OAuth client</summary>
          <Hint className="mt-2">
            Only for servers without dynamic client registration. Register an OAuth app with redirect URI{' '}
            <code>{`${window.location.origin}/auth/mcp/callback`}</code>.
          </Hint>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-2">
            <Field label="Client ID">
              <Input value={clientId} onChange={e => setClientId(e.target.value)} />
            </Field>
            <Field label="Client secret">
              <Input type="password" value={clientSecret} onChange={e => setClientSecret(e.target.value)} />
            </Field>
            <Field label="Scopes">
              <Input placeholder="as the server suggests" value={scopes} onChange={e => setScopes(e.target.value)} />
            </Field>
          </div>
        </details>
      )}
      {server && choice !== 'keep' && (
        <Notice tone="warning">Saving replaces this server's saved credentials.</Notice>
      )}
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
      <FormActions>
        <Button variant="primary" type="submit" disabled={busy || !url.trim()}>
          {busy ? 'Connecting...' : !server ? 'Add server' : choice === 'keep' ? 'Save' : 'Save and reconnect'}
        </Button>
      </FormActions>
    </form>
  )
}
