import { useEffect, useState } from 'react'
import { usePeople } from '@/hooks/usePeople'
import { PersonRef } from '@/hooks/useSources'
import { Button, Checkbox, Field, MultiSelect } from '@/components/shared/ui'

const SEARCH_DELAY_MS = 250

interface TaskPeopleFieldProps {
  createTasks: boolean
  people: PersonRef[]
  /** The account owner's own Person, offered as "Only me" or "Only the owner". */
  owner: PersonRef | null
  /** Whether the viewer owns the account. */
  ownerIsYou: boolean
  onChange: (createTasks: boolean, people: PersonRef[]) => void
}

/** Whether a transcript account turns action items into tasks, and for whom. */
export const TaskPeopleField = ({ createTasks, people, owner, ownerIsYou, onChange }: TaskPeopleFieldProps) => {
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

  const known = new Map([...found, ...people, ...(owner ? [owner] : [])].map(p => [String(p.id), p]))
  const options = [...known.values()].map(p => ({
    value: String(p.id),
    label: p.display_name,
    description: p.id === owner?.id ? (ownerIsYou ? 'You' : 'Owner') : undefined,
  }))
  const onlyOwner = owner && !(people.length === 1 && people[0].id === owner.id)

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
          hint="Leave empty to make a task for every action item. Once anyone is picked, action items assigned to someone else or to nobody make no task. An assignee matches by full name, alias or email, so add the name you go by in meetings as an alias."
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
      {createTasks && onlyOwner && (
        <Button size="sm" onClick={() => onChange(true, [owner])}>
          {ownerIsYou ? 'Only me' : 'Only the owner'} ({owner.display_name})
        </Button>
      )}
    </div>
  )
}
