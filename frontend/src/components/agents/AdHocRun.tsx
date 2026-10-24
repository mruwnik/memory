import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { defaultSpec, useAgents, type AgentSpec, type Catalog } from '@/hooks/useAgents'
import { SpecEditor } from './SpecEditor'
import { Button, Field, FormActions, Input, Modal, Notice, Page, Textarea, useSubmit } from '@/components/shared/ui'

function SaveAsAgent({ spec, onClose }: { spec: AgentSpec; onClose: () => void }) {
  const navigate = useNavigate()
  const { saveDefinition } = useAgents()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const { busy, error, submit } = useSubmit()

  const save = () => submit(async () => {
    navigate(`/ui/agents/${(await saveDefinition({ name: name.trim(), description, spec })).id}`)
  })

  return (
    <Modal title="Save as agent" onClose={onClose}>
      <form className="space-y-4" onSubmit={e => { e.preventDefault(); save() }}>
        {error && <Notice tone="error">{error}</Notice>}
        <Field label="Name"><Input required maxLength={100} value={name} onChange={e => setName(e.target.value)} /></Field>
        <Field label="Description"><Input value={description} onChange={e => setDescription(e.target.value)} /></Field>
        <FormActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={busy || !name.trim()}>{busy ? 'Saving...' : 'Save agent'}</Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default function AdHocRun() {
  const navigate = useNavigate()
  const { catalog: loadCatalog, startRun } = useAgents()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [spec, setSpec] = useState<AgentSpec | null>(null)
  const [input, setInput] = useState('')
  const [valid, setValid] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const running = useSubmit()

  const load = useCallback(() => {
    setLoadError(null)
    loadCatalog()
      .then(c => {
        setCatalog(c)
        setSpec(defaultSpec(c.default_model))
      })
      .catch(e => setLoadError(e.message))
  }, [loadCatalog])
  useEffect(load, [load])

  const page = {
    back: { to: '/ui/agents/runs', label: 'Runs' },
    title: 'Ad-hoc run',
    description: 'Try a one-off configuration without saving an agent.',
  }
  if (!catalog || !spec) return <Page {...page} loading error={loadError} onRetry={load} />

  const run = () => running.submit(async () => navigate(`/ui/agents/runs/${(await startRun({ input, spec })).run_id}`))

  return (
    <Page {...page}>
      <SpecEditor value={spec} catalog={catalog} onChange={setSpec} onValidityChange={setValid} />
      <Field label="Run input">
        <Textarea className="h-24" placeholder="What should the agent do?" value={input} onChange={e => setInput(e.target.value)} />
      </Field>
      <FormActions>
        {running.error && <Notice tone="error" className="basis-full">{running.error}</Notice>}
        <Button disabled={!valid} onClick={() => setSaving(true)}>Save as agent</Button>
        <Button variant="primary" disabled={running.busy || !input.trim() || !valid} onClick={run}>
          {running.busy ? 'Starting...' : 'Run'}
        </Button>
      </FormActions>
      {saving && <SaveAsAgent spec={spec} onClose={() => setSaving(false)} />}
    </Page>
  )
}
