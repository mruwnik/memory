import { useState, useEffect } from 'react'
import type { Person } from '@/hooks/usePeople'
import { Button, Combobox, Field, Input, List, Row } from '@/components/shared/ui'

const errorText = (e: unknown) => e instanceof Error ? e.message : String(e)

const SEARCH_DEBOUNCE_MS = 300

export type ListPeople = (filters?: { search?: string; limit?: number }) => Promise<Person[]>

/** Searches people by name and offers each match with an "Add" action. */
export const PersonSearch = ({ label, listPeople, exclude, onAdd }: {
  label: string
  listPeople: ListPeople
  exclude: Set<number>
  onAdd: (person: Person) => void
}) => {
  const [term, setTerm] = useState('')
  const [results, setResults] = useState<Person[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!term.trim()) {
      setResults(null)
      return
    }
    // A response for an older term must not overwrite a newer one
    let cancelled = false
    const timer = setTimeout(async () => {
      setError(null)
      try {
        const people = await listPeople({ search: term, limit: 10 })
        if (!cancelled) setResults(people)
      } catch (e) {
        if (cancelled) return
        setError(`Search failed: ${errorText(e)}`)
        setResults(null)
      }
    }, SEARCH_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [term, listPeople])

  const matches = results?.filter(p => !exclude.has(p.id))
  const add = (person: Person) => {
    onAdd(person)
    setTerm('')
  }

  return (
    <div className="space-y-2">
      <Field label={label} error={error}>
        <Input type="search" value={term} onChange={e => setTerm(e.target.value)} placeholder="Search people by name"
          onKeyDown={e => e.key === 'Enter' && e.preventDefault()} />
      </Field>
      <p role="status" className="sr-only">{matches && `${matches.length} match${matches.length !== 1 ? 'es' : ''}`}</p>
      {matches && (matches.length === 0
        ? <p className="text-sm text-slate-600">No matching people</p>
        : (
          <List className="max-h-48 overflow-y-auto">
            {matches.map(person => (
              <Row key={person.id} title={person.display_name} meta={[`@${person.identifier}`]}
                actions={<Button type="button" size="sm" onClick={() => add(person)} aria-label={`Add ${person.display_name}`}>Add</Button>} />
            ))}
          </List>
        ))}
    </div>
  )
}

interface PersonRef {
  id: number
  identifier: string
  display_name: string | null
}

const personLabel = (p: PersonRef) => p.display_name || p.identifier

/** Single-person picker that searches by name as you type, so it works past the list limit. */
export const PersonSelect = ({ value, current, listPeople, onChange, ...fieldProps }: {
  value: number | null
  /** The person already stored for `value`, shown without a search. */
  current?: PersonRef | null
  listPeople: ListPeople
  onChange: (id: number | null) => void
  // Wired in by the surrounding Field
  id?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}) => {
  const [error, setError] = useState<string | null>(null)
  const [found, setFound] = useState<PersonRef[]>([])
  const [loading, setLoading] = useState(false)
  const [term, setTerm] = useState('')

  useEffect(() => {
    if (!term.trim()) {
      setFound([])
      setError(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    // A response for an older term must not overwrite a newer one
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const people = await listPeople({ search: term, limit: 10 })
        if (!cancelled) setFound(people)
      } catch (e) {
        if (cancelled) return
        setFound([])
        setError(`Search failed: ${errorText(e)}`)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, SEARCH_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [term, listPeople])

  const people = current && !found.some(p => p.id === current.id) ? [current, ...found] : found
  const options = people.map(p => ({ value: String(p.id), label: personLabel(p) }))

  return (
    <div className="space-y-1">
      <div className="flex gap-2">
        <Combobox {...fieldProps} className="flex-1" value={value == null ? '' : String(value)} options={options} loading={loading}
          onQueryChange={setTerm} placeholder="Search people by name" onChange={v => onChange(Number(v))} />
        {value != null && <Button type="button" size="sm" onClick={() => onChange(null)} aria-label="Clear owner">Clear</Button>}
      </div>
      {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    </div>
  )
}
