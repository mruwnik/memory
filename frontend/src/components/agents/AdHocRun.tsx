import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { defaultSpec, useAgents, type AgentSpec, type Catalog } from '@/hooks/useAgents'
import { SpecEditor } from './SpecEditor'
import { Button, Field, Notice, Page, Textarea } from '@/components/shared/ui'

export default function AdHocRun() {
  const navigate = useNavigate()
  const { catalog: loadCatalog, startRun, saveDefinition } = useAgents()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [spec, setSpec] = useState<AgentSpec | null>(null)
  const [input, setInput] = useState('')
  const [jsonValid, setJsonValid] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadCatalog()
      .then(c => {
        setCatalog(c)
        setSpec(defaultSpec(c.default_model))
      })
      .catch(e => setError(e.message))
  }, [loadCatalog])

  const act = async (fn: () => Promise<void>) => {
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const page = {
    back: { to: '/ui/agents/runs', label: 'Runs' },
    title: 'Ad-hoc run',
    description: 'Try a one-off configuration without saving an agent.',
  }
  if (!catalog || !spec) return <Page {...page} loading error={error} />

  return (
    <Page {...page}>
      {error && <Notice tone="error">{error}</Notice>}
      <SpecEditor value={spec} catalog={catalog} onChange={setSpec} onValidityChange={setJsonValid} />
      <Field label="Run input">
        <Textarea className="h-24" placeholder="What should the agent do?" value={input} onChange={e => setInput(e.target.value)} />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={!input.trim() || !jsonValid}
          onClick={() => act(async () => navigate(`/ui/agents/runs/${(await startRun({ input, spec })).run_id}`))}>Run</Button>
        <Button disabled={!jsonValid}
          onClick={() => act(async () => {
            const name = window.prompt('Name for this agent')
            if (name) navigate(`/ui/agents/${(await saveDefinition({ name, description: '', spec })).id}`)
          })}>Save as agent</Button>
      </div>
    </Page>
  )
}
