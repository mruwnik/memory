import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { defaultSpec, useAgents, type AgentRun, type AgentSpec, type Catalog } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { SpecEditor } from './SpecEditor'
import { StatusBadge } from './StatusBadge'

export default function AgentEditor() {
  const { id } = useParams()
  const isNew = id === 'new'
  const definitionId = isNew ? undefined : Number(id)
  const validId = isNew || Number.isInteger(definitionId)
  const navigate = useNavigate()
  const { catalog: loadCatalog, getDefinition, saveDefinition, deleteDefinition, startRun, listRuns } = useAgents()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [spec, setSpec] = useState<AgentSpec | null>(null)
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [jsonValid, setJsonValid] = useState(true)
  const [saved, setSaved] = useState<string | null>(null)
  const current = useMemo(() => JSON.stringify({ name, description, spec }), [name, description, spec])
  const dirty = current !== saved

  useEffect(() => {
    loadCatalog()
      .then(c => {
        setCatalog(c)
        if (isNew) setSpec(defaultSpec(c.default_model))
      })
      .catch(e => setError(e.message))
  }, [loadCatalog, isNew])

  useEffect(() => {
    if (definitionId === undefined || !validId) return
    getDefinition(definitionId)
      .then(d => {
        setName(d.name)
        setDescription(d.description)
        setSpec(d.spec)
        setSaved(JSON.stringify({ name: d.name, description: d.description, spec: d.spec }))
      })
      .catch(e => setError(e.message))
    listRuns({ definitionId, limit: 20 }).then(setRuns).catch(e => setError(e.message))
  }, [definitionId, validId, getDefinition, listRuns])

  const save = async () => {
    if (!spec) return
    setError(null)
    try {
      const stored = await saveDefinition({ name, description, spec, id: definitionId })
      setName(stored.name)
      setSpec(stored.spec)
      setSaved(JSON.stringify({ name: stored.name, description: stored.description, spec: stored.spec }))
      if (isNew) navigate(`/ui/agents/${stored.id}`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const run = async () => {
    setError(null)
    try {
      const { run_id } = await startRun({ input, definitionId })
      navigate(`/ui/agents/runs/${run_id}`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const remove = async () => {
    if (definitionId === undefined || !window.confirm(`Delete agent "${name}"? Its runs are kept.`)) return
    try {
      await deleteDefinition(definitionId)
      navigate('/ui/agents')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!validId) return <div className="p-6 text-slate-500">Unknown agent</div>
  if (!catalog || !spec) return <div className="p-6 text-slate-500">{error ?? 'Loading agent...'}</div>

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      <Link to="/ui/agents" className="text-sm text-primary">← Agents</Link>
      {error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm whitespace-pre-wrap">{error}</div>}
      <div className="grid grid-cols-2 gap-3">
        <input aria-label="Name" maxLength={100} className="px-3 py-1.5 border rounded" placeholder="name" value={name} onChange={e => setName(e.target.value)} />
        <input aria-label="Description" className="px-3 py-1.5 border rounded" placeholder="description" value={description} onChange={e => setDescription(e.target.value)} />
      </div>
      <SpecEditor value={spec} catalog={catalog} onChange={setSpec} onValidityChange={setJsonValid} />
      <div className="flex gap-2">
        <button className="px-3 py-1.5 bg-primary text-white rounded text-sm disabled:opacity-50"
          disabled={!jsonValid || !dirty} onClick={save}>Save</button>
        {!isNew && <button className="px-3 py-1.5 border border-red-300 text-red-700 rounded text-sm" onClick={remove}>Delete</button>}
      </div>
      {!isNew && (
        <section className="space-y-3">
          <h2 className="font-semibold">Run</h2>
          <textarea aria-label="Run input" className="w-full px-3 py-1.5 border rounded h-24" value={input} onChange={e => setInput(e.target.value)} />
          <button className="px-3 py-1.5 bg-primary text-white rounded text-sm disabled:opacity-50"
            disabled={!input.trim() || dirty} onClick={run}>Run saved version</button>
          {dirty && <span className="ml-2 text-xs text-slate-500">Save your changes to run them.</span>}
          <ul className="divide-y bg-white rounded-xl shadow-sm">
            {runs.map(r => (
              <li key={r.id} className="p-3 flex justify-between text-sm">
                <Link to={`/ui/agents/runs/${r.id}`} className="text-primary truncate max-w-md">{r.input}</Link>
                <span className="flex items-center gap-2">
                  <StatusBadge status={r.status} />
                  <span className="text-xs text-slate-400">{formatRelativeTime(r.created_at)} · ${r.cost_usd}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
