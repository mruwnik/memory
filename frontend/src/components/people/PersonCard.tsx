import type { Person, Tidbit } from '../../hooks/usePeople'
import type { Team } from '../../hooks/useTeams'
import { Badge, Button, ConfirmButton, DetailList, Row, SubList, type Tone } from '@/components/shared/ui'

interface PersonCardProps {
  person: Person
  expanded: boolean
  onToggleExpand: () => void
  onEdit: () => void
  onDelete: () => void
  teams?: Team[]
  teamsLoading?: boolean
  tidbits?: Tidbit[]
  tidbitsLoading?: boolean
  onAddTidbit: () => void
  /** Whether the current user may change this tidbit (the backend allows only its creator or an admin). */
  canEditTidbit: (tidbit: Tidbit) => boolean
  onEditTidbit: (tidbit: Tidbit) => void
  onDeleteTidbit: (tidbit: Tidbit) => void
  /** Selection checkbox for bulk actions. */
  select?: React.ReactNode
}

const SENSITIVITY_TONES: Record<string, Tone> = { confidential: 'danger', internal: 'warning' }

const contactLink = (key: string, value: string) =>
  key === 'email' ? <a href={`mailto:${value}`} className="text-primary hover:underline">{value}</a>
    : key === 'phone' ? <a href={`tel:${value}`} className="text-primary hover:underline">{value}</a>
    : value

const PersonCard = ({ person, expanded, onToggleExpand, onEdit, onDelete, teams, teamsLoading, tidbits, tidbitsLoading, onAddTidbit, canEditTidbit, onEditTidbit, onDeleteTidbit, select }: PersonCardProps) => {
  // Nested objects (e.g. Slack workspace data) aren't contact details
  const contacts = Object.entries(person.contact_info ?? {}).filter((e): e is [string, string] => typeof e[1] === 'string')

  return (
    <Row
      leading={<div className="flex items-center gap-3">
        {select}
        <span aria-hidden="true" className="w-10 h-10 bg-primary/10 rounded-full flex items-center justify-center text-primary font-semibold">
          {person.display_name.charAt(0).toUpperCase()}
        </span>
      </div>}
      title={person.display_name}
      onSelect={onToggleExpand}
      expanded={expanded}
      badges={person.tags?.map(tag => <Badge key={tag}>{tag}</Badge>)}
      meta={[`@${person.identifier}`]}
      actions={<>
        <Button size="sm" onClick={onEdit}>Edit</Button>
        <ConfirmButton confirm={`Delete ${person.display_name}? This cannot be undone.`} onConfirm={onDelete} />
      </>}
    >
      {expanded && (
        <>
          <DetailList items={[
            ['Also known as', !!person.aliases?.length && (
              <span className="flex flex-wrap gap-1">{person.aliases.map((alias, i) => <Badge key={i}>{alias}</Badge>)}</span>
            )],
            ...contacts.map(([key, value]): [string, React.ReactNode] => [key.charAt(0).toUpperCase() + key.slice(1), contactLink(key, value)]),
            ['Teams', teamsLoading ? 'Loading teams...' : !!teams?.length && (
              <span className="flex flex-wrap gap-1">{teams.map(team => <Badge key={team.id} tone="primary">{team.name}</Badge>)}</span>
            )],
            ['Added', person.created_at && new Date(person.created_at).toLocaleDateString()],
          ]} />
          <SubList title={tidbits?.length ? `Tidbits (${tidbits.length})` : 'Tidbits'}
            actions={<Button size="sm" onClick={onAddTidbit}>Add tidbit</Button>} empty={tidbitsLoading ? 'Loading tidbits...' : 'No tidbits yet'}>
            {!tidbitsLoading && tidbits?.map(tidbit => (
              <Row
                key={tidbit.id}
                title={<span className="font-normal whitespace-pre-wrap">{tidbit.content}</span>}
                badges={<>
                  {tidbit.tidbit_type && <Badge>{tidbit.tidbit_type}</Badge>}
                  {tidbit.sensitivity && tidbit.sensitivity !== 'basic' && (
                    <Badge tone={SENSITIVITY_TONES[tidbit.sensitivity] ?? 'warning'}>{tidbit.sensitivity}</Badge>
                  )}
                  {tidbit.tags?.map((tag, i) => <Badge tone="info" key={i}>{tag}</Badge>)}
                </>}
                meta={[tidbit.source, tidbit.created_at && new Date(tidbit.created_at).toLocaleDateString()]}
                actions={canEditTidbit(tidbit) && <>
                  <Button size="sm" onClick={() => onEditTidbit(tidbit)} aria-label={`Edit tidbit: ${tidbit.content.slice(0, 40)}`}>Edit</Button>
                  <ConfirmButton aria-label={`Delete tidbit: ${tidbit.content.slice(0, 40)}`} confirm="Delete this tidbit? This cannot be undone." onConfirm={() => onDeleteTidbit(tidbit)} />
                </>}
              />
            ))}
          </SubList>
        </>
      )}
    </Row>
  )
}

export default PersonCard
