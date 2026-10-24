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
  /** Selection checkbox for bulk actions. */
  select?: React.ReactNode
}

const SENSITIVITY_TONES: Record<string, Tone> = { confidential: 'danger', internal: 'warning' }

const contactLink = (key: string, value: string) =>
  key === 'email' ? <a href={`mailto:${value}`} className="text-primary hover:underline">{value}</a>
    : key === 'phone' ? <a href={`tel:${value}`} className="text-primary hover:underline">{value}</a>
    : value

const PersonCard = ({ person, expanded, onToggleExpand, onEdit, onDelete, teams, teamsLoading, tidbits, tidbitsLoading, select }: PersonCardProps) => {
  const hasDetails = (person.aliases && person.aliases.length > 0) ||
    (person.contact_info && Object.keys(person.contact_info).length > 0) ||
    (teams && teams.length > 0) ||
    (tidbits && tidbits.length > 0)

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
      onSelect={hasDetails ? onToggleExpand : undefined}
      expanded={hasDetails ? expanded : undefined}
      badges={person.tags?.map(tag => <Badge key={tag}>{tag}</Badge>)}
      meta={[`@${person.identifier}`]}
      actions={<>
        <Button size="sm" onClick={onEdit}>Edit</Button>
        <ConfirmButton confirm={`Delete ${person.display_name}? This cannot be undone.`} onConfirm={onDelete} />
      </>}
    >
      {expanded && hasDetails && (
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
          {(tidbitsLoading || !!tidbits?.length) && (
            <SubList title={tidbitsLoading ? 'Tidbits' : `Tidbits (${tidbits!.length})`} empty={tidbitsLoading && 'Loading tidbits...'}>
              {!tidbitsLoading && tidbits!.map(tidbit => (
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
                  meta={[tidbit.source, tidbit.inserted_at && new Date(tidbit.inserted_at).toLocaleDateString()]}
                />
              ))}
            </SubList>
          )}
        </>
      )}
    </Row>
  )
}

export default PersonCard
