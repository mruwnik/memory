import { useEffect, useState } from 'react'
import { useSetup } from '@/hooks/useSetup'
import { CopyButton, Disclosure, LoadingState, Notice } from '@/components/shared/ui'

export const POLL_MS = 3000

const cursorConfig = (url: string) => JSON.stringify({ mcpServers: { memory: { url } } }, null, 2)

const Snippet = ({ text }: { text: string }) => (
  <div className="flex items-start gap-2">
    <pre className="flex-1 min-w-0 whitespace-pre-wrap wrap-anywhere rounded-lg bg-slate-100 p-3 text-sm">{text}</pre>
    <CopyButton text={text} />
  </div>
)

export const ClientStep = () => {
  const { getStatus } = useSetup()
  const [url, setUrl] = useState<string | null>(null)
  const [clients, setClients] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  // Polls until the client's first OAuth sign-in shows up as a grant for this user.
  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const poll = () => getStatus().then(
      s => {
        if (stopped) return
        setError(null)
        setUrl(s.mcp_url)
        setClients(s.mcp_clients)
        if (!s.mcp_clients.length) timer = setTimeout(poll, POLL_MS)
      },
      e => {
        if (stopped) return
        setError(e.message)
        timer = setTimeout(poll, POLL_MS)
      },
    )
    poll()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [getStatus])

  if (!url) return error ? <Notice tone="error">{error}</Notice> : <LoadingState />
  return (
    <>
      <p className="text-sm text-slate-600">
        Add this server to your AI client. The first time it connects, it opens a browser window for you to sign in.
      </p>
      <Snippet text={url} />
      <Disclosure summary="Claude Code" open>
        <Snippet text={`claude mcp add --transport http memory ${url}`} />
      </Disclosure>
      <Disclosure summary="Cursor">
        <p className="text-sm text-slate-600 mb-2">Add this to <code>~/.cursor/mcp.json</code>.</p>
        <Snippet text={cursorConfig(url)} />
      </Disclosure>
      <Disclosure summary="Claude Desktop or claude.ai">
        <p className="text-sm text-slate-600">In Settings, under Connectors, add a custom connector and paste the URL above.</p>
      </Disclosure>
      {error && <Notice tone="error">{error}</Notice>}
      {clients.length
        ? <Notice tone="success">Connected: {clients.join(', ')}</Notice>
        : <Notice tone="info">Waiting for your client to connect...</Notice>}
    </>
  )
}
