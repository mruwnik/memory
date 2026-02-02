import { useState, useEffect, useCallback } from 'react'
import { useTeams, Team, TeamCreate, TeamUpdate, TeamMember, TeamProject } from '@/hooks/useTeams'
import { usePeople, Person } from '@/hooks/usePeople'
import { useProjects, Project } from '@/hooks/useProjects'
import {
  Modal,
  EmptyState,
  LoadingState,
  ErrorState,
  ConfirmDialog,
} from '../shared'
import { styles, cx } from '../styles'

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

  const { listPeople } = usePeople()
  const { listProjects } = useProjects()

  const [teams, setTeams] = useState<Team[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showInactive, setShowInactive] = useState(false)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingTeam, setEditingTeam] = useState<Team | null>(null)
  const [managingMembers, setManagingMembers] = useState<Team | null>(null)
  const [managingProjects, setManagingProjects] = useState<Team | null>(null)
  const [archivingTeam, setArchivingTeam] = useState<Team | null>(null)

  const loadTeams = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const teamsList = await listTeams({ include_inactive: showInactive, include_projects: true })
      setTeams(Array.isArray(teamsList) ? teamsList : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load teams')
    } finally {
      setLoading(false)
    }
  }, [listTeams, showInactive])

  useEffect(() => {
    loadTeams()
  }, [loadTeams])

  const handleCreate = async (data: TeamCreate) => {
    const result = await createTeam(data)
    if (result.success) {
      setShowCreateModal(false)
      loadTeams()
    } else {
      throw new Error(result.error || 'Failed to create team')
    }
  }

  const handleUpdate = async (slug: string, data: TeamUpdate) => {
    const result = await updateTeam(slug, data)
    if (result.success) {
      setEditingTeam(null)
      loadTeams()
    } else {
      throw new Error(result.error || 'Failed to update team')
    }
  }

  const handleArchive = async (team: Team) => {
    await updateTeam(team.slug, { is_active: false })
    setArchivingTeam(null)
    loadTeams()
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadTeams} />

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Teams</h3>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={e => setShowInactive(e.target.checked)}
              className="rounded border-slate-300"
            />
            Show archived
          </label>
          <button className={styles.btnAdd} onClick={() => setShowCreateModal(true)}>
            New Team
          </button>
        </div>
      </div>

      {teams.length === 0 ? (
        <EmptyState
          message="No teams found. Create a team to organize people and manage project access."
          actionLabel="Create Team"
          onAction={() => setShowCreateModal(true)}
        />
      ) : (
        <div className={styles.sourceList}>
          {teams.map(team => (
            <TeamCard
              key={team.id}
              team={team}
              onEdit={() => setEditingTeam(team)}
              onManageMembers={() => setManagingMembers(team)}
              onManageProjects={() => setManagingProjects(team)}
              onArchive={() => setArchivingTeam(team)}
            />
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <TeamFormModal
          onSubmit={handleCreate}
          onClose={() => setShowCreateModal(false)}
          listPeople={listPeople}
          addMember={addMember}
        />
      )}

      {/* Edit Modal */}
      {editingTeam && (
        <TeamFormModal
          team={editingTeam}
          onSubmit={data => handleUpdate(editingTeam.slug, data)}
          onClose={() => setEditingTeam(null)}
          listPeople={listPeople}
          addMember={addMember}
        />
      )}

      {/* Manage Members Modal */}
      {managingMembers && (
        <MembersModal
          team={managingMembers}
          onClose={() => {
            setManagingMembers(null)
            loadTeams()
          }}
          addMember={addMember}
          removeMember={removeMember}
          listPeople={listPeople}
          getTeam={getTeam}
        />
      )}

      {/* Archive Confirmation */}
      {archivingTeam && (
        <ConfirmDialog
          title="Archive Team"
          message={`Are you sure you want to archive "${archivingTeam.name}"? Members will lose access to projects through this team.`}
          confirmLabel="Archive"
          onConfirm={() => handleArchive(archivingTeam)}
          onCancel={() => setArchivingTeam(null)}
        />
      )}

      {/* Manage Projects Modal */}
      {managingProjects && (
        <TeamProjectsModal
          team={managingProjects}
          onClose={() => {
            setManagingProjects(null)
            loadTeams()
          }}
          getTeam={getTeam}
          listProjects={listProjects}
          assignTeam={assignTeamToProject}
          unassignTeam={unassignTeamFromProject}
        />
      )}
    </div>
  )
}

// Team Card Component
const TeamCard = ({
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
}) => {
  return (
    <div className={team.is_active ? styles.card : styles.cardInactive}>
      <div className={styles.cardHeader}>
        <div className={styles.cardInfo}>
          <div className="flex items-center gap-2">
            <h4 className={styles.cardTitle}>{team.name}</h4>
            {!team.is_active && (
              <span className={cx(styles.badge, styles.badgeInactive)}>Archived</span>
            )}
          </div>
          <p className={styles.cardSubtitle}>@{team.slug}</p>
          {team.description && (
            <p className="text-sm text-slate-600 mt-1">{team.description}</p>
          )}
          {team.tags && team.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {team.tags.map(tag => (
                <span key={tag} className="bg-slate-100 text-slate-600 px-2 py-0.5 rounded text-xs">
                  {tag}
                </span>
              ))}
            </div>
          )}
          <div className="flex items-center gap-4 mt-2 text-xs text-slate-500">
            {team.member_count !== undefined && (
              <span>{team.member_count} member{team.member_count !== 1 ? 's' : ''}</span>
            )}
            {team.discord_role_id && (
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 bg-indigo-500 rounded-full"></span>
                Discord
              </span>
            )}
            {team.github_team_id && (
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 bg-slate-800 rounded-full"></span>
                GitHub
              </span>
            )}
          </div>
          {team.projects && team.projects.length > 0 && (
            <div className="flex items-center gap-1.5 mt-2 flex-wrap">
              <span className="text-xs text-slate-500">Projects:</span>
              {team.projects.slice(0, 3).map(project => (
                <span
                  key={project.id}
                  className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded text-xs cursor-pointer hover:bg-blue-100"
                  onClick={onManageProjects}
                >
                  {project.title}
                </span>
              ))}
              {team.projects.length > 3 && (
                <span
                  className="text-xs text-slate-500 cursor-pointer hover:text-slate-700"
                  onClick={onManageProjects}
                  title={team.projects.slice(3).map(p => p.title).join(', ')}
                >
                  +{team.projects.length - 3} more
                </span>
              )}
            </div>
          )}
        </div>
        <div className={styles.cardActions}>
          <button className={styles.btnEdit} onClick={onManageMembers}>
            Members
          </button>
          <button className={styles.btnEdit} onClick={onManageProjects}>
            Projects
          </button>
          <button className={styles.btnEdit} onClick={onEdit}>
            Edit
          </button>
          {team.is_active && (
            <button className={styles.btnDelete} onClick={onArchive}>
              Archive
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// Team Form Modal
const TeamFormModal = ({
  team,
  onSubmit,
  onClose,
  listPeople,
  addMember,
}: {
  team?: Team
  onSubmit: (data: TeamCreate | TeamUpdate) => Promise<void>
  onClose: () => void
  listPeople: (filters?: { search?: string; limit?: number }) => Promise<Person[]>
  addMember: (team: string, person: string, role?: string) => Promise<{ success: boolean; error?: string }>
}) => {
  const [name, setName] = useState(team?.name || '')
  const [slug, setSlug] = useState(team?.slug || '')
  const [description, setDescription] = useState(team?.description || '')
  const [tags, setTags] = useState(team?.tags?.join(', ') || '')
  const [discordRoleId, setDiscordRoleId] = useState(team?.discord_role_id?.toString() || '')
  const [discordGuildId, setDiscordGuildId] = useState(team?.discord_guild_id?.toString() || '')
  const [autoSyncDiscord, setAutoSyncDiscord] = useState(team?.auto_sync_discord ?? true)
  const [githubTeamId, setGithubTeamId] = useState(team?.github_team_id?.toString() || '')
  const [githubOrg, setGithubOrg] = useState(team?.github_org || '')
  const [autoSyncGithub, setAutoSyncGithub] = useState(team?.auto_sync_github ?? true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Member management state (for new teams)
  const [pendingMembers, setPendingMembers] = useState<Person[]>([])
  const [memberSearch, setMemberSearch] = useState('')
  const [memberSearchResults, setMemberSearchResults] = useState<Person[]>([])
  const [searchingMembers, setSearchingMembers] = useState(false)

  const isEditing = !!team

  // Auto-generate slug from name if not editing
  useEffect(() => {
    if (!isEditing && name) {
      setSlug(name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''))
    }
  }, [name, isEditing])

  // Search for people to add as members
  useEffect(() => {
    if (!memberSearch.trim()) {
      setMemberSearchResults([])
      return
    }

    const timer = setTimeout(async () => {
      setSearchingMembers(true)
      try {
        const people = await listPeople({ search: memberSearch, limit: 10 })
        // Filter out already pending members
        const pendingIds = new Set(pendingMembers.map(p => p.id))
        setMemberSearchResults(people.filter(p => !pendingIds.has(p.id)))
      } catch {
        // Ignore search errors
      } finally {
        setSearchingMembers(false)
      }
    }, 300)

    return () => clearTimeout(timer)
  }, [memberSearch, listPeople, pendingMembers])

  const handleAddPendingMember = (person: Person) => {
    setPendingMembers(prev => [...prev, person])
    setMemberSearch('')
    setMemberSearchResults([])
  }

  const handleRemovePendingMember = (personId: number) => {
    setPendingMembers(prev => prev.filter(p => p.id !== personId))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)

    try {
      const data: TeamCreate | TeamUpdate = {
        name,
        ...(isEditing ? {} : { slug }),
        description: description || undefined,
        tags: tags ? tags.split(',').map(t => t.trim()).filter(Boolean) : undefined,
        discord_role_id: discordRoleId ? parseInt(discordRoleId) : undefined,
        discord_guild_id: discordGuildId ? parseInt(discordGuildId) : undefined,
        auto_sync_discord: autoSyncDiscord,
        github_team_id: githubTeamId ? parseInt(githubTeamId) : undefined,
        github_org: githubOrg || undefined,
        auto_sync_github: autoSyncGithub,
      }
      await onSubmit(data)

      // If creating a new team with pending members, add them after creation
      if (!isEditing && pendingMembers.length > 0) {
        const results = await Promise.allSettled(
          pendingMembers.map(person => addMember(slug, person.identifier))
        )
        const failures = results.filter(
          (r): r is PromiseRejectedResult => r.status === 'rejected'
        )
        if (failures.length > 0) {
          console.warn('Some members failed to add:', failures.map(f => f.reason))
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save team')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={isEditing ? 'Edit Team' : 'Create Team'} onClose={onClose}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Name *</label>
          <input
            type="text"
            className={styles.formInput}
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="Engineering Core"
            required
          />
        </div>

        {!isEditing && (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Slug *</label>
            <input
              type="text"
              className={styles.formInput}
              value={slug}
              onChange={e => setSlug(e.target.value)}
              placeholder="engineering-core"
              pattern="[a-z0-9-]+"
              required
            />
            <p className={styles.formHint}>URL-safe identifier, auto-generated from name</p>
          </div>
        )}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Description</label>
          <textarea
            className={styles.formTextarea}
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="Core engineering team responsible for..."
            rows={2}
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Tags</label>
          <input
            type="text"
            className={styles.formInput}
            value={tags}
            onChange={e => setTags(e.target.value)}
            placeholder="engineering, core, platform"
          />
          <p className={styles.formHint}>Comma-separated tags for filtering</p>
        </div>

        {/* Member selection for new teams */}
        {!isEditing && (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Initial Members</label>
            <input
              type="text"
              className={styles.formInput}
              value={memberSearch}
              onChange={e => setMemberSearch(e.target.value)}
              placeholder="Search for people to add..."
            />
            {searchingMembers && <p className="text-xs text-slate-500 mt-1">Searching...</p>}
            {memberSearchResults.length > 0 && (
              <div className="mt-2 border border-slate-200 rounded-lg max-h-32 overflow-y-auto">
                {memberSearchResults.map(person => (
                  <button
                    key={person.id}
                    type="button"
                    className="w-full text-left px-3 py-2 hover:bg-slate-50 flex items-center justify-between"
                    onClick={() => handleAddPendingMember(person)}
                  >
                    <span>
                      <span className="font-medium">{person.display_name}</span>
                      <span className="text-slate-500 ml-2">@{person.identifier}</span>
                    </span>
                    <span className="text-primary text-sm">Add</span>
                  </button>
                ))}
              </div>
            )}
            {pendingMembers.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {pendingMembers.map(person => (
                  <span
                    key={person.id}
                    className="bg-purple-100 text-purple-700 px-2 py-1 rounded-full text-sm flex items-center gap-1"
                  >
                    {person.display_name}
                    <button
                      type="button"
                      className="hover:text-purple-900 ml-1"
                      onClick={() => handleRemovePendingMember(person.id)}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <p className={styles.formHint}>
              You can add more members after creation via the Members button
            </p>
          </div>
        )}

        <details className={styles.detailsSection}>
          <summary className={styles.detailsSummary}>Discord Integration</summary>
          <div className={cx(styles.detailsContent, 'space-y-3')}>
            <div className={styles.formRow}>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Role ID</label>
                <input
                  type="text"
                  className={styles.formInput}
                  value={discordRoleId}
                  onChange={e => setDiscordRoleId(e.target.value)}
                  placeholder="123456789"
                />
              </div>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Guild ID</label>
                <input
                  type="text"
                  className={styles.formInput}
                  value={discordGuildId}
                  onChange={e => setDiscordGuildId(e.target.value)}
                  placeholder="987654321"
                />
              </div>
            </div>
            <label className={styles.formCheckbox}>
              <input
                type="checkbox"
                checked={autoSyncDiscord}
                onChange={e => setAutoSyncDiscord(e.target.checked)}
              />
              <span className="text-sm">Auto-sync membership changes to Discord</span>
            </label>
          </div>
        </details>

        <details className={styles.detailsSection}>
          <summary className={styles.detailsSummary}>GitHub Integration</summary>
          <div className={cx(styles.detailsContent, 'space-y-3')}>
            <div className={styles.formRow}>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Team ID</label>
                <input
                  type="text"
                  className={styles.formInput}
                  value={githubTeamId}
                  onChange={e => setGithubTeamId(e.target.value)}
                  placeholder="42"
                />
              </div>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Organization</label>
                <input
                  type="text"
                  className={styles.formInput}
                  value={githubOrg}
                  onChange={e => setGithubOrg(e.target.value)}
                  placeholder="myorg"
                />
              </div>
            </div>
            <label className={styles.formCheckbox}>
              <input
                type="checkbox"
                checked={autoSyncGithub}
                onChange={e => setAutoSyncGithub(e.target.checked)}
              />
              <span className="text-sm">Auto-sync membership changes to GitHub</span>
            </label>
          </div>
        </details>

        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={styles.btnSubmit} disabled={submitting}>
            {submitting ? 'Saving...' : isEditing ? 'Save Changes' : 'Create Team'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// Members Management Modal
const MembersModal = ({
  team,
  onClose,
  addMember,
  removeMember,
  listPeople,
  getTeam,
}: {
  team: Team
  onClose: () => void
  addMember: (team: string, person: string, role?: string) => Promise<{ success: boolean; error?: string }>
  removeMember: (team: string, person: string) => Promise<{ success: boolean; error?: string }>
  listPeople: (filters?: { search?: string; limit?: number }) => Promise<Person[]>
  getTeam: (team: string, includeMembers?: boolean) => Promise<Team | null>
}) => {
  const [members, setMembers] = useState<TeamMember[]>(team.members || [])
  const [searchTerm, setSearchTerm] = useState('')
  const [searchResults, setSearchResults] = useState<Person[]>([])
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load members
  useEffect(() => {
    const loadMembers = async () => {
      const teamData = await getTeam(team.slug, true)
      if (teamData?.members) {
        setMembers(teamData.members)
      }
    }
    loadMembers()
  }, [team.slug, getTeam])

  // Search for people
  useEffect(() => {
    if (!searchTerm.trim()) {
      setSearchResults([])
      return
    }

    const timer = setTimeout(async () => {
      setSearching(true)
      try {
        const people = await listPeople({ search: searchTerm, limit: 10 })
        // Filter out existing members
        const memberIds = new Set(members.map(m => m.id))
        setSearchResults(people.filter(p => !memberIds.has(p.id)))
      } catch {
        // Ignore search errors
      } finally {
        setSearching(false)
      }
    }, 300)

    return () => clearTimeout(timer)
  }, [searchTerm, listPeople, members])

  const handleAddMember = async (person: Person) => {
    setError(null)
    const result = await addMember(team.slug, person.identifier)
    if (result.success) {
      setMembers(prev => [...prev, {
        id: person.id,
        identifier: person.identifier,
        display_name: person.display_name,
      }])
      setSearchTerm('')
      setSearchResults([])
    } else {
      setError(result.error || 'Failed to add member')
    }
  }

  const handleRemoveMember = async (member: TeamMember) => {
    setError(null)
    const result = await removeMember(team.slug, member.identifier)
    if (result.success) {
      setMembers(prev => prev.filter(m => m.id !== member.id))
    } else {
      setError(result.error || 'Failed to remove member')
    }
  }

  return (
    <Modal title={`Members of ${team.name}`} onClose={onClose}>
      <div className="space-y-4">
        {error && <div className={styles.formError}>{error}</div>}

        {/* Search to add */}
        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Add Member</label>
          <input
            type="text"
            className={styles.formInput}
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            placeholder="Search for people..."
          />
          {searching && <p className="text-xs text-slate-500 mt-1">Searching...</p>}
          {searchResults.length > 0 && (
            <div className="mt-2 border border-slate-200 rounded-lg max-h-40 overflow-y-auto">
              {searchResults.map(person => (
                <button
                  key={person.id}
                  type="button"
                  className="w-full text-left px-3 py-2 hover:bg-slate-50 flex items-center justify-between"
                  onClick={() => handleAddMember(person)}
                >
                  <span>
                    <span className="font-medium">{person.display_name}</span>
                    <span className="text-slate-500 ml-2">@{person.identifier}</span>
                  </span>
                  <span className="text-primary text-sm">Add</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Current members */}
        <div>
          <h4 className="text-sm font-medium text-slate-700 mb-2">
            Current Members ({members.length})
          </h4>
          {members.length === 0 ? (
            <p className="text-sm text-slate-500">No members yet</p>
          ) : (
            <div className="space-y-2">
              {members.map(member => (
                <div
                  key={member.id}
                  className="flex items-center justify-between py-2 px-3 bg-slate-50 rounded-lg"
                >
                  <div>
                    <span className="font-medium">{member.display_name}</span>
                    <span className="text-slate-500 text-sm ml-2">@{member.identifier}</span>
                    {member.contributor_status && (
                      <span className="ml-2 text-xs bg-slate-200 text-slate-600 px-1.5 py-0.5 rounded">
                        {member.contributor_status}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    className="text-red-600 text-sm hover:text-red-700"
                    onClick={() => handleRemoveMember(member)}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className={styles.formActions}>
          <button type="button" className={styles.btnPrimary} onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </Modal>
  )
}

// Team Projects Management Modal
const TeamProjectsModal = ({
  team,
  onClose,
  getTeam,
  listProjects,
  assignTeam,
  unassignTeam,
}: {
  team: Team
  onClose: () => void
  getTeam: (team: string | number, includeMembers?: boolean, includeProjects?: boolean) => Promise<Team | null>
  listProjects: (options?: { state?: string; include_children?: boolean }) => Promise<Project[]>
  assignTeam: (project: number, teamId: number) => Promise<{ success: boolean; error?: string }>
  unassignTeam: (project: number, teamId: number) => Promise<{ success: boolean; error?: string }>
}) => {
  const [assignedProjects, setAssignedProjects] = useState<TeamProject[]>([])
  const [allProjects, setAllProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Load data on mount
  useEffect(() => {
    const loadData = async () => {
      setLoading(true)
      try {
        const [teamData, projects] = await Promise.all([
          getTeam(team.slug, false, true),
          listProjects({ state: 'open' }),
        ])
        setAssignedProjects(teamData?.projects || [])
        setAllProjects(projects)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load data')
      } finally {
        setLoading(false)
      }
    }
    loadData()
  }, [team.slug, getTeam, listProjects])

  const handleAssign = async (project: Project) => {
    setError(null)
    const result = await assignTeam(project.id, team.id)
    if (result.success) {
      setAssignedProjects(prev => [...prev, {
        id: project.id,
        title: project.title,
        state: project.state,
        repo_path: project.repo_path,
      }])
    } else {
      setError(result.error || 'Failed to assign project')
    }
  }

  const handleUnassign = async (project: TeamProject) => {
    setError(null)
    const result = await unassignTeam(project.id, team.id)
    if (result.success) {
      setAssignedProjects(prev => prev.filter(p => p.id !== project.id))
    } else {
      setError(result.error || 'Failed to unassign project')
    }
  }

  const assignedIds = new Set(assignedProjects.map(p => p.id))
  const availableProjects = allProjects.filter(p => !assignedIds.has(p.id))

  return (
    <Modal title={`Projects for ${team.name}`} onClose={onClose}>
      <div className="space-y-4">
        {error && <div className={styles.formError}>{error}</div>}

        {loading ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : (
          <>
            {/* Assigned projects */}
            <div>
              <h4 className="text-sm font-medium text-slate-700 mb-2">
                Assigned Projects ({assignedProjects.length})
              </h4>
              {assignedProjects.length === 0 ? (
                <p className="text-sm text-slate-500">No projects assigned</p>
              ) : (
                <div className="space-y-2">
                  {assignedProjects.map(project => (
                    <div
                      key={project.id}
                      className="flex items-center justify-between py-2 px-3 bg-blue-50 rounded-lg"
                    >
                      <div>
                        <span className="font-medium text-blue-900">{project.title}</span>
                        {project.repo_path && (
                          <span className="text-blue-600 text-sm ml-2">({project.repo_path})</span>
                        )}
                      </div>
                      <button
                        type="button"
                        className="text-red-600 text-sm hover:text-red-700"
                        onClick={() => handleUnassign(project)}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Available projects to add */}
            {availableProjects.length > 0 && (
              <div>
                <h4 className="text-sm font-medium text-slate-700 mb-2">
                  Available Projects
                </h4>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {availableProjects.map(project => (
                    <div
                      key={project.id}
                      className="flex items-center justify-between py-2 px-3 bg-slate-50 rounded-lg"
                    >
                      <div>
                        <span className="font-medium">{project.title}</span>
                        {project.repo_path && (
                          <span className="text-slate-500 text-sm ml-2">({project.repo_path})</span>
                        )}
                      </div>
                      <button
                        type="button"
                        className="text-primary text-sm hover:text-primary/80"
                        onClick={() => handleAssign(project)}
                      >
                        Add
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        <div className={styles.formActions}>
          <button type="button" className={styles.btnPrimary} onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </Modal>
  )
}
