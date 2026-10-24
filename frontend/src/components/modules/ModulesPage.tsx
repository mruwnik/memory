import { useCallback, useEffect, useState } from 'react'
import { Badge, Card, Hint, List, Page, Row } from '@/components/shared/ui'
import { apiError } from '@/hooks/apiError'
import { useAuth } from '@/hooks/useAuth'

interface ModuleInfo {
  name: string
  description: string
  enabled: boolean
}

const ModulesPage = () => {
  const { apiCall } = useAuth()
  const [modules, setModules] = useState<ModuleInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const response = await apiCall('/modules')
      if (!response.ok) throw await apiError(response, 'Failed to load modules')
      setModules(await response.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load modules')
    }
  }, [apiCall])

  useEffect(() => { load() }, [load])

  const envLine = `ENABLE_MODULES=${(modules ?? []).filter(m => m.enabled).map(m => m.name).sort().join(',')}`
  return (
    <Page title="Modules" description="Optional features this instance runs. Every module is off by default."
      loading={modules === null && !error} error={error} onRetry={load}>
      <List>
        {(modules ?? []).map(({ name, description, enabled }) => (
          <Row key={name} title={<code>{name}</code>}
            badges={enabled ? <Badge tone="success">on</Badge> : <Badge>off</Badge>}
            meta={[description]} />
        ))}
      </List>
      <Card title="Change modules">
        <code className="block bg-slate-100 px-3 py-2 rounded text-sm font-mono break-all">{envLine}</code>
        <Hint className="mt-2">
          Set this in the server's environment and restart every service. Modules can't be changed from the web UI.
          <code>claude_containers</code> also needs the <code>docker-compose.claude.yaml</code> overlay.
        </Hint>
      </Card>
    </Page>
  )
}

export default ModulesPage
