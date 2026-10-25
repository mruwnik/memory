import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { KEY_PROVIDERS, PROVIDER_NAMES, useAgents, type KeyUpsert, type ProviderKey, type SystemAgentEntry } from '@/hooks/useAgents'
import { AddKeyForm } from '@/components/agents/KeysPage'
import { SystemAgents } from '@/components/agents/SystemAgents'
import { Badge, Card, List, LoadingState, Notice, Row } from '@/components/shared/ui'

export const KeysStep = () => {
  const { listKeys, upsertKey, listSystemAgents } = useAgents()
  const [keys, setKeys] = useState<ProviderKey[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [systemAgents, setSystemAgents] = useState<SystemAgentEntry[] | null>(null)
  const [systemError, setSystemError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoadError(null)
    setSystemError(null)
    listKeys()
      .then(result => setKeys(KEY_PROVIDERS.flatMap(p => result.providers[p])))
      .catch(e => setLoadError(e.message))
    listSystemAgents().then(setSystemAgents).catch(e => setSystemError(e.message))
  }, [listKeys, listSystemAgents])
  useEffect(load, [load])

  const save = async (body: KeyUpsert) => {
    await upsertKey(body)
    load()
  }

  return (
    <>
      <Notice tone="info">
        Semantic search also needs a Voyage key, which an admin sets on the{' '}
        <Link to="/ui/instance-settings" target="_blank" className="underline">Instance settings page (opens in a new tab)</Link>.
        Until then search matches keywords only.
      </Notice>
      {loadError && <Notice tone="error" onRetry={load}>{loadError}</Notice>}
      {keys === null && !loadError && <LoadingState message="Loading keys..." />}
      {!!keys?.length && (
        <List aria-label="Your keys">
          {keys.map(k => (
            <Row key={k.id} title={k.label}
              badges={<>
                <Badge tone="neutral">{PROVIDER_NAMES[k.provider]}</Badge>
                {k.status === 'ok' ? <Badge tone="success">OK</Badge> : <Badge tone="danger">Rejected</Badge>}
              </>}
              meta={[`••••${k.key_hint}`]} />
          ))}
        </List>
      )}
      <Card title={keys?.length ? 'Add another key' : 'Add a key'}>
        {/* Remounted per saved key, so the form clears for the next one. */}
        <AddKeyForm key={keys?.length} initialProvider="anthropic" onSave={save} />
      </Card>
      {/* With no key every system agent reads "can't run", which says nothing the step doesn't. */}
      {!!keys?.length && (
        <SystemAgents agents={systemAgents} error={systemError} onRetry={load}
          description="What memory runs for you, and the model your default keys run each on." />
      )}
    </>
  )
}
