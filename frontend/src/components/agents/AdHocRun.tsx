import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { defaultSpec, useAgents, type AgentSpec, type Catalog } from '@/hooks/useAgents'
import { SpecEditor } from './SpecEditor'

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

  if (!catalog || !spec) return <div className="p-6 text-slate-500">{error ?? 'Loading...'}</div>

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-4">
      <Link to="/ui/agents/runs" className="text-sm text-primary">← Runs</Link>
      <h1 className="text-2xl font-semibold">Ad-hoc run</h1>
      {error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm whitespace-pre-wrap">{error}</div>}
      <SpecEditor value={spec} catalog={catalog} onChange={setSpec} onValidityChange={setJsonValid} />
      <textarea aria-label="Run input" className="w-full px-3 py-1.5 border rounded h-24" placeholder="What should the agent do?"
        value={input} onChange={e => setInput(e.target.value)} />
      <div className="flex gap-2">
        <button className="px-3 py-1.5 bg-primary text-white rounded text-sm disabled:opacity-50" disabled={!input.trim() || !jsonValid}
          onClick={() => act(async () => navigate(`/ui/agents/runs/${(await startRun({ input, spec })).run_id}`))}>Run</button>
        <button className="px-3 py-1.5 border rounded text-sm disabled:opacity-50" disabled={!jsonValid}
          onClick={() => act(async () => {
            const name = window.prompt('Name for this agent')
            if (name) navigate(`/ui/agents/${(await saveDefinition({ name, description: '', spec })).id}`)
          })}>Save as definition</button>
      </div>
    </div>
  )
}
