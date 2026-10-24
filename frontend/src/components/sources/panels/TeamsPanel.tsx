import { useState, useEffect, useCallback } from 'react'
import { useTeams, Team, TeamCreate, TeamUpdate, TeamMember, TeamProject, TeamSaved } from '@/hooks/useTeams'
import { usePeople, Person, Tidbit } from '@/hooks/usePeople'
import { useProjects, Project } from '@/hooks/useProjects'
import { Badge, Button, Checkbox, ConfirmButton, DetailList, Disclosure, EmptyState, Field, List, LoadingState, Page, Row, SubList, Toolbar, FieldRow, FormActions, Input, Modal, Notice, Select, Textarea, useActionNotice } from '@/components/shared/ui'

const errorText = (e: unknown) => e instanceof Error ? e.message : String(e)

export const TeamsPanel = () => {
  const {
    listTeams,
    getTeam,
    createTeam,
    updateTeam,
    addMember,
    removeMember,
    assignTeamToProject,
    unassignTeamFromProject,
  } = useTeams()

  const { listPeople, getPerson } = usePeople()
  const { listProjects } = useProjects()
  const { run, notice } = useActionNotice()

  const [teams, setTeams] = useState<Team[]>([])
  const [availablePeople, setAvailablePeople] = useState<Person[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [showInactive, setShowInactive] = useState(false)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingTeam, setEditingTeam] = useState<Team | null>(null)
  const [managingMembers, setManagingMembers] = useState<Team | null>(null)
  const [managingProjects, setManagingProjects] = useState<Team | null>(null)

  const loadTeams = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [teamsList, people] = await Promise.all([
        listTeams({ include_inactive: showInactive, include_projects: true }),
        listPeople({ limit: 100 }),
      ])
      setTeams(teamsList)
      setAvailablePeople(people)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load teams')
    } finally {
      setLoading(false)
    }
  }, [listTeams, listPeople, showInactive])

  useEffect(() => {
    loadTeams()
  }, [loadTeams])

  const saved = (result: TeamSaved, extraWarnings: string[] = []) => {
    setWarnings([...(result.warnings ?? []), ...extraWarnings])
    setShowCreateModal(false)
    setEditingTeam(null)
    loadTeams()
  }

  const handleCreate = async (data: TeamCreate, members: Person[]) => {
    const result = await createTeam(data)
    const slug = result.team?.slug ?? data.slug ?? ''
    const added = await Promise.allSettled(members.map(person => addMember(slug, person.identifier)))
    const failed = added.flatMap((r, i) =>
      r.status === 'rejected' ? [`Couldn't add ${members[i].display_name || members[i].identifier}: ${errorText(r.reason)}`] : [])
    saved(result, failed)
  }

  const handleUpdate = async (slug: string, data: TeamUpdate) => saved(await updateTeam(slug, data))

  const handleArchive = (team: Team) => run(async () => {
    await updateTeam(team.slug, { is_active: false })
    await loadTeams()
  }, `Archived ${team.name}`)

  return (
    <Page
      title="Teams"
      actions={<Button variant="primary" onClick={() => setShowCreateModal(true)}>New team</Button>}
      width="wide"
      loading={loading}
      error={error}
      onRetry={loadTeams}
    >
      <Toolbar>
        <Checkbox checked={showInactive} onChange={e => setShowInactive(e.target.checked)} label="Show archived" />
      </Toolbar>

      {notice}
      {warnings.length > 0 && (
        <Notice tone="warning" onDismiss={() => setWarnings([])}>
          Team saved, but:{'\n'}{warnings.join('\n')}
        </Notice>
      )}

      {teams.length === 0 ? (
        <EmptyState
          message="No teams found. Create a team to organize people and manage project access."
          actionLabel="New team"
          onAction={() => setShowCreateModal(true)}
        />
      ) : (
        <List>
          {teams.map(team => (
            <TeamRow
              key={team.id}
              team={team}
              onEdit={() => setEditingTeam(team)}
              onManageMembers={() => setManagingMembers(team)}
              onManageProjects={() => setManagingProjects(team)}
              onArchive={() => handleArchive(team)}
            />
          ))}
        </List>
      )}

      {showCreateModal && (
        <TeamFormModal
          onSubmit={(data, members) => handleCreate(data as TeamCreate, members)}
          onClose={() => setShowCreateModal(false)}
          listPeople={listPeople}
          availablePeople={availablePeople}
        />
      )}

      {editingTeam && (
        <TeamFormModal
          team={editingTeam}
          onSubmit={data => handleUpdate(editingTeam.slug, data)}
          onClose={() => setEditingTeam(null)}
          listPeople={listPeople}
          availablePeople={availablePeople}
        />
      )}

      {managingMembers && (
        <MembersModal
          team={managingMembers}
          onClose={(hasChanges) => {
            setManagingMembers(null)
            if (hasChanges) loadTeams()
          }}
          addMember={addMember}
          removeMember={removeMember}
          listPeople={listPeople}
          getTeam={getTeam}
          getPerson={getPerson}
        />
      )}

      {managingProjects && (
        <TeamProjectsModal
          team={managingProjects}
          onClose={(hasChanges) => {
            setManagingProjects(null)
            if (hasChanges) loadTeams()
          }}
          getTeam={getTeam}
          listProjects={listProjects}
          assignTeam={assignTeamToProject}
          unassignTeam={unassignTeamFromProject}
        />
      )}
    </Page>
  )
}

const TeamRow = ({
  team,
  onEdit,
  onManageMembers,
  onManageProjects,
  onArchive,
}: {
  team: Team
  onEdit: () => void
  onManageMembers: () => void
  onManageProjects: () => void
  onArchive: () => void
}) => (
  <Row
    title={team.name}
    muted={!team.is_active}
    badges={<>
      {!team.is_active && <Badge>Archived</Badge>}
      {team.discord_role_id && <Badge tone="info">Discord</Badge>}
      {team.github_team_slug && <Badge>GitHub</Badge>}
    </>}
    meta={[
      `@${team.slug}`,
      team.owner && `Owner: ${team.owner.display_name || team.owner.identifier}`,
      team.member_count !== undefined && `${team.member_count} member${team.member_count !== 1 ? 's' : ''}`,
      !!team.projects?.length && `Projects: ${team.projects.map(p => p.title).join(', ')}`,
    ]}
    actions={<>
      <Button size="sm" onClick={onManageMembers}>Members</Button>
      <Button size="sm" onClick={onManageProjects}>Projects</Button>
      <Button size="sm" onClick={onEdit}>Edit</Button>
      {team.is_active && (
        <ConfirmButton confirm={`Archive "${team.name}"? Members will lose access to projects through this team.`} onConfirm={onArchive}>Archive</ConfirmButton>
      )}
    </>}
  >
    {team.description && <p className="text-slate-600 line-clamp-2">{team.description}</p>}
    {!!team.tags?.length && <div className="flex flex-wrap gap-1">{team.tags.map(tag => <Badge key={tag}>{tag}</Badge>)}</div>}
  </Row>
)

type ListPeople = (filters?: { search?: string; limit?: number }) => Promise<Person[]>

/** Searches people by name and offers each match with an "Add" action. */
const PersonSearch = ({ label, listPeople, exclude, onAdd }: {
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
    }, 300)
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

const TeamFormModal = ({
  team,
  onSubmit,
  onClose,
  listPeople,
  availablePeople = [],
}: {
  team?: Team
  onSubmit: (data: TeamCreate | TeamUpdate, members: Person[]) => Promise<void>
  onClose: () => void
  listPeople: ListPeople
  availablePeople?: Person[]
}) => {
  const initialGuild = team?.discord_guild_id ?? ''
  const initialRole = team?.discord_role_id ?? ''
  const initialOrg = team?.github_org ?? ''
  const initialGithubTeam = team?.github_team_slug ?? ''
  const initialAutoSyncDiscord = team?.auto_sync_discord ?? false
  const initialAutoSyncGithub = team?.auto_sync_github ?? false

  const [name, setName] = useState(team?.name || '')
  const [slug, setSlug] = useState(team?.slug || '')
  const [description, setDescription] = useState(team?.description || '')
  const [ownerId, setOwnerId] = useState<number | null>(team?.owner_id ?? null)
  const [tags, setTags] = useState(team?.tags?.join(', ') || '')
  const [guild, setGuild] = useState(initialGuild)
  const [discordRole, setDiscordRole] = useState(initialRole)
  const [autoSyncDiscord, setAutoSyncDiscord] = useState(initialAutoSyncDiscord)
  const [githubOrg, setGithubOrg] = useState(initialOrg)
  const [githubTeam, setGithubTeam] = useState(initialGithubTeam)
  const [autoSyncGithub, setAutoSyncGithub] = useState(initialAutoSyncGithub)
  const [pendingMembers, setPendingMembers] = useState<Person[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEditing = !!team

  useEffect(() => {
    if (!isEditing && name) {
      setSlug(name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''))
    }
  }, [name, isEditing])

  // A role is looked up in a server, so a new one can't be saved without it. An existing role is
  // fine: clearing the server unlinks Discord, role included.
  const roleError = discordRole.trim() && discordRole !== initialRole && !guild.trim() ? 'Set the server too' : null
  const githubTeamError = githubTeam.trim() && githubTeam !== initialGithubTeam && !githubOrg.trim() ? 'Set the organization too' : null

  // Send an integration only when it changed: each save re-resolves it against Discord/GitHub and,
  // with auto-sync on, syncs membership (so turning auto-sync on runs a sync). Empty strings unlink it.
  const discordChanged = guild !== initialGuild || discordRole !== initialRole || autoSyncDiscord !== initialAutoSyncDiscord
  const githubChanged = githubOrg !== initialOrg || githubTeam !== initialGithubTeam || autoSyncGithub !== initialAutoSyncGithub

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (roleError || githubTeamError) return
    setSubmitting(true)
    setError(null)

    try {
      const data: TeamCreate | TeamUpdate = {
        name,
        ...(isEditing ? {} : { slug }),
        description,
        ...(isEditing ? { owner: ownerId } : ownerId != null ? { owner: ownerId } : {}),
        tags: tags.split(',').map(t => t.trim()).filter(Boolean),
        ...(discordChanged && { guild: guild.trim(), discord_role: discordRole.trim() }),
        auto_sync_discord: autoSyncDiscord,
        ...(githubChanged && { github_org: githubOrg.trim(), github_team_slug: githubTeam.trim() }),
        auto_sync_github: autoSyncGithub,
      }
      await onSubmit(data, pendingMembers)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save team')
      setSubmitting(false)
    }
  }

  return (
    <Modal title={isEditing ? 'Edit team' : 'New team'} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Name">
          <Input
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="Engineering Core"
            required
          />
        </Field>

        {!isEditing && (
          <Field label="Slug" hint="URL-safe identifier, auto-generated from name">
            <Input
              type="text"
              value={slug}
              onChange={e => setSlug(e.target.value)}
              placeholder="engineering-core"
              pattern="[a-z0-9-]+"
              required
            />
          </Field>
        )}

        <Field label="Description">
          <Textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="Core engineering team responsible for..."
            rows={2}
          />
        </Field>

        <Field label="Tags" hint="Comma-separated tags for filtering">
          <Input
            type="text"
            value={tags}
            onChange={e => setTags(e.target.value)}
            placeholder="engineering, core, platform"
          />
        </Field>

        <Field label="Owner" hint="Assign someone responsible for this team">
          <Select
            value={ownerId ?? ''}
            onChange={e => setOwnerId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">No owner</option>
            {availablePeople.map(person => (
              <option key={person.id} value={person.id}>
                {person.display_name || person.identifier}
              </option>
            ))}
          </Select>
        </Field>

        {!isEditing && (
          <div className="space-y-2">
            <PersonSearch label="Initial members" listPeople={listPeople}
              exclude={new Set(pendingMembers.map(p => p.id))}
              onAdd={person => setPendingMembers(prev => [...prev, person])} />
            <SubList title={`To add (${pendingMembers.length})`} empty="You can also add members later from Members.">
              {pendingMembers.map(person => (
                <Row key={person.id} title={person.display_name} meta={[`@${person.identifier}`]}
                  actions={<Button type="button" size="sm" aria-label={`Don't add ${person.display_name}`}
                    onClick={() => setPendingMembers(prev => prev.filter(p => p.id !== person.id))}>Remove</Button>} />
              ))}
            </SubList>
          </div>
        )}

        <Disclosure summary="Discord integration" open={!!initialGuild}>
          <div className="space-y-3">
            <FieldRow>
              <Field label="Server" hint="Server ID or name. Clear it to unlink Discord.">
                <Input type="text" value={guild} onChange={e => setGuild(e.target.value)} />
              </Field>
              <Field label="Role" hint="Role ID or name; a new name creates the role" error={roleError}>
                <Input type="text" value={discordRole} onChange={e => setDiscordRole(e.target.value)} />
              </Field>
            </FieldRow>
            <Checkbox checked={autoSyncDiscord} onChange={e => setAutoSyncDiscord(e.target.checked)} label="Auto-sync membership changes to Discord" />
          </div>
        </Disclosure>

        <Disclosure summary="GitHub integration" open={!!initialOrg}>
          <div className="space-y-3">
            <FieldRow>
              <Field label="Organization" hint="Clear it to unlink GitHub.">
                <Input type="text" value={githubOrg} onChange={e => setGithubOrg(e.target.value)} placeholder="myorg" />
              </Field>
              <Field label="Team slug" hint="Created in GitHub if it doesn't exist" error={githubTeamError}>
                <Input type="text" value={githubTeam} onChange={e => setGithubTeam(e.target.value)} placeholder="core-team" />
              </Field>
            </FieldRow>
            <Checkbox checked={autoSyncGithub} onChange={e => setAutoSyncGithub(e.target.checked)} label="Auto-sync membership changes to GitHub" />
          </div>
        </Disclosure>

        <FormActions>
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={submitting}>
            {submitting ? 'Saving...' : isEditing ? 'Save changes' : 'Create team'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

const MembersModal = ({
  team,
  onClose,
  addMember,
  removeMember,
  listPeople,
  getTeam,
  getPerson,
}: {
  team: Team
  onClose: (hasChanges: boolean) => void
  addMember: (team: string, person: string) => Promise<void>
  removeMember: (team: string, person: string) => Promise<void>
  listPeople: ListPeople
  getTeam: (team: string, includeMembers?: boolean) => Promise<Team | null>
  getPerson: GetPerson
}) => {
  const [members, setMembers] = useState<TeamMember[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selected, setSelected] = useState<TeamMember | null>(null)
  const [hasChanges, setHasChanges] = useState(false)
  const { run, notice } = useActionNotice()

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const teamData = await getTeam(team.slug, true)
      if (!teamData) throw new Error(`Team not found: ${team.slug}`)
      setMembers(teamData.members ?? [])
    } catch (e) {
      setLoadError(`Couldn't load the members: ${errorText(e)}`)
    }
  }, [team.slug, getTeam])

  useEffect(() => {
    load()
  }, [load])

  const handleAdd = (person: Person) => run(async () => {
    await addMember(team.slug, person.identifier)
    setMembers(prev => [...(prev ?? []), { id: person.id, identifier: person.identifier, display_name: person.display_name }])
    setHasChanges(true)
  })

  const handleRemove = (member: TeamMember) => run(async () => {
    await removeMember(team.slug, member.identifier)
    setMembers(prev => (prev ?? []).filter(m => m.id !== member.id))
    setHasChanges(true)
  })

  const handleClose = () => onClose(hasChanges)

  return (
    <Modal title={`Members of ${team.name}`} onClose={handleClose}>
      <div className="space-y-4">
        {notice}

        {loadError ? <Notice tone="error" onRetry={load}>{loadError}</Notice>
          : members === null ? <LoadingState message="Loading members..." /> : (
          <>
            <PersonSearch label="Add member" listPeople={listPeople}
              exclude={new Set(members.map(m => m.id))} onAdd={handleAdd} />

            <SubList title={`Members (${members.length})`} empty="No members yet">
              {members.map(member => (
                <Row key={member.id} title={member.display_name} meta={[`@${member.identifier}`]}
                  onSelect={() => setSelected(member)}
                  badges={<>
                    {member.role && <Badge tone="primary">{member.role}</Badge>}
                    {member.contributor_status && <Badge>{member.contributor_status}</Badge>}
                  </>}
                  actions={<ConfirmButton aria-label={`Remove ${member.display_name}`} onConfirm={() => handleRemove(member)}
                    confirm={`Remove ${member.display_name} from ${team.name}? They lose access to its projects.`}>Remove</ConfirmButton>} />
              ))}
            </SubList>
          </>
        )}

        <FormActions>
          <Button variant="primary" type="button" onClick={handleClose}>
            Done
          </Button>
        </FormActions>
      </div>
      {selected && <PersonDetailsModal member={selected} getPerson={getPerson} onClose={() => setSelected(null)} />}
    </Modal>
  )
}

type GetPerson = (identifier: string, includeTidbits?: boolean) => Promise<Person | null>

const PersonDetailsModal = ({ member, getPerson, onClose }: { member: TeamMember; getPerson: GetPerson; onClose: () => void }) => {
  const [person, setPerson] = useState<Person | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const found = await getPerson(member.identifier, true)
      if (!found) throw new Error(`Person not found: ${member.identifier}`)
      setPerson(found)
    } catch (e) {
      setError(`Couldn't load ${member.display_name}: ${errorText(e)}`)
    }
  }, [member.identifier, member.display_name, getPerson])

  useEffect(() => {
    load()
  }, [load])

  const contacts = Object.entries(person?.contact_info ?? {}).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
  return (
    <Modal title={person?.display_name || member.display_name} onClose={onClose} size="md">
      {error ? <Notice tone="error" onRetry={load}>{error}</Notice>
        : !person ? <LoadingState message="Loading person..." /> : (
          <div className="space-y-4">
            <DetailList items={[
              ['Identifier', `@${person.identifier}`],
              ['Also known as', person.aliases?.join(', ')],
              ...contacts.map(([key, value]): [string, string] => [key.charAt(0).toUpperCase() + key.slice(1), value]),
              ['Tags', !!person.tags?.length && <div className="flex flex-wrap gap-1">{person.tags.map(tag => <Badge key={tag}>{tag}</Badge>)}</div>],
            ]} />
            {!!person.tidbits?.length && (
              <SubList title={`Tidbits (${person.tidbits.length})`} scroll>
                {person.tidbits.map((tidbit: Tidbit) => (
                  <Row key={tidbit.id} title={<span className="font-normal whitespace-pre-wrap">{tidbit.content}</span>}
                    badges={<>
                      {tidbit.tidbit_type && <Badge>{tidbit.tidbit_type}</Badge>}
                      {tidbit.tags?.map(tag => <Badge key={tag} tone="info">{tag}</Badge>)}
                    </>}
                    meta={[tidbit.source, tidbit.created_at && new Date(tidbit.created_at).toLocaleDateString()]} />
                ))}
              </SubList>
            )}
          </div>
        )}
    </Modal>
  )
}

const TeamProjectsModal = ({
  team,
  onClose,
  getTeam,
  listProjects,
  assignTeam,
  unassignTeam,
}: {
  team: Team
  onClose: (hasChanges: boolean) => void
  getTeam: (team: string | number, includeMembers?: boolean, includeProjects?: boolean) => Promise<Team | null>
  listProjects: (options?: { state?: 'open' | 'closed' }) => Promise<Project[]>
  assignTeam: (project: number, teamId: number) => Promise<void>
  unassignTeam: (project: number, teamId: number) => Promise<void>
}) => {
  const [assignedProjects, setAssignedProjects] = useState<TeamProject[] | null>(null)
  const [allProjects, setAllProjects] = useState<Project[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [hasChanges, setHasChanges] = useState(false)
  const { run, notice } = useActionNotice()

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const [teamData, projects] = await Promise.all([
        getTeam(team.slug, false, true),
        listProjects({ state: 'open' }),
      ])
      if (!teamData) throw new Error(`Team not found: ${team.slug}`)
      setAssignedProjects(teamData.projects ?? [])
      setAllProjects(projects)
    } catch (e) {
      setLoadError(`Couldn't load the projects: ${errorText(e)}`)
    }
  }, [team.slug, getTeam, listProjects])

  useEffect(() => {
    load()
  }, [load])

  const handleAssign = (project: Project) => run(async () => {
    await assignTeam(project.id, team.id)
    setAssignedProjects(prev => [...(prev ?? []), { id: project.id, title: project.title, state: project.state, repo_path: project.repo_path }])
    setHasChanges(true)
  })

  const handleUnassign = (project: TeamProject) => run(async () => {
    await unassignTeam(project.id, team.id)
    setAssignedProjects(prev => (prev ?? []).filter(p => p.id !== project.id))
    setHasChanges(true)
  })

  const assignedIds = new Set(assignedProjects?.map(p => p.id))
  const availableProjects = allProjects.filter(p => !assignedIds.has(p.id))

  const handleClose = () => onClose(hasChanges)

  return (
    <Modal title={`Projects for ${team.name}`} onClose={handleClose}>
      <div className="space-y-4">
        {notice}

        {loadError ? <Notice tone="error" onRetry={load}>{loadError}</Notice>
          : assignedProjects === null ? <LoadingState message="Loading projects..." /> : (
          <>
            <SubList title={`Assigned projects (${assignedProjects.length})`} empty="No projects assigned">
              {assignedProjects.map(project => (
                <Row key={project.id} title={project.title} meta={[project.repo_path]}
                  actions={<ConfirmButton aria-label={`Remove ${project.title}`} onConfirm={() => handleUnassign(project)}
                    confirm={`Remove ${project.title} from ${team.name}? Members lose access to it through this team.`}>Remove</ConfirmButton>} />
              ))}
            </SubList>

            {availableProjects.length > 0 && (
              <SubList title="Available projects" scroll>
                {availableProjects.map(project => (
                  <Row key={project.id} title={project.title} meta={[project.repo_path]}
                    actions={<Button size="sm" aria-label={`Add ${project.title}`} onClick={() => handleAssign(project)}>Add</Button>} />
                ))}
              </SubList>
            )}
          </>
        )}

        <FormActions>
          <Button variant="primary" type="button" onClick={handleClose}>
            Done
          </Button>
        </FormActions>
      </div>
    </Modal>
  )
}
