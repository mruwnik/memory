import { useEffect, useState } from 'react'
import { usePeople } from '@/hooks/usePeople'
import { PersonRef } from '@/hooks/useSources'
import { Button, Checkbox, Field, MultiSelect } from '@/components/shared/ui'

const SEARCH_DELAY_MS = 250

interface TaskPeopleFieldProps {
  createTasks: boolean
  people: PersonRef[]
  /** The account owner's own Person, offered as "Only me". */
  me: PersonRef | null
  onChange: (createTasks: boolean, people: PersonRef[]) => void
}

/** Whether a transcript account turns action items into tasks, and for whom. */
export const TaskPeopleField = ({ createTasks, people, me, onChange }: TaskPeopleFieldProps) => {
  const { listPeople } = usePeople()
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<PersonRef[]>([])

  useEffect(() => {
    let live = true
    const timer = setTimeout(() => {
      listPeople({ search: query.trim() || undefined, limit: 20 })
        .then(results => live && setFound(results.map(p => ({ id: p.id, display_name: p.display_name }))))
        // Without the people scope the search fails; "Only me" still works.
        .catch(() => live && setFound([]))
    }, SEARCH_DELAY_MS)
    return () => { live = false; clearTimeout(timer) }
  }, [listPeople, query])

  const known = new Map([...found, ...people, ...(me ? [me] : [])].map(p => [String(p.id), p]))
  const options = [...known.values()].map(p => ({
    value: String(p.id),
    label: p.display_name,
    description: p.id === me?.id ? 'You' : undefined,
  }))
  const onlyMe = me && !(people.length === 1 && people[0].id === me.id)

  return (
    <div className="space-y-3">
      <Checkbox
        label="Create tasks from action items"
        checked={createTasks}
        onChange={e => onChange(e.target.checked, people)}
      />
      {createTasks && (
        <Field
          label="Only for these people"
          hint="Leave empty to make a task for every action item. Once anyone is picked, action items assigned to someone else or to nobody make no task."
        >
          <MultiSelect
            options={options}
            value={people.map(p => String(p.id))}
            onChange={ids => onChange(createTasks, ids.flatMap(id => known.get(id) ?? []))}
            onQueryChange={setQuery}
            placeholder="Everyone"
          />
        </Field>
      )}
      {createTasks && onlyMe && (
        <Button size="sm" onClick={() => onChange(true, [me])}>Only me ({me.display_name})</Button>
      )}
    </div>
  )
}
