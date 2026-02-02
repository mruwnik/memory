import { useState, useEffect, useCallback } from 'react'
import { useProjects, Project, ProjectTreeNode, ProjectCreate, ProjectUpdate } from '@/hooks/useProjects'
import { useTeams, Team } from '@/hooks/useTeams'
import {
  Modal,
  EmptyState,
  LoadingState,
  ErrorState,
  ConfirmDialog,
} from '../shared'
import { styles, cx } from '../styles'

export const ProjectsPanel = () => {
  const {
    listProjects,
    getProjectTree,
    createProject,
    updateProject,
    deleteProject,
  } = useProjects()

  const {
    listTeams,
    listProjectTeams,
    assignTeamToProject,
    unassignTeamFromProject,
  } = useTeams()

  const [projects, setProjects] = useState<Project[]>([])
  const [tree, setTree] = useState<ProjectTreeNode[]>([])
  const [availableTeams, setAvailableTeams] = useState<Team[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'tree' | 'list'>('list')
  const [stateFilter, setStateFilter] = useState<'all' | 'open' | 'closed'>('all')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [deletingProject, setDeletingProject] = useState<Project | null>(null)
  const [managingTeams, setManagingTeams] = useState<Project | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const state = stateFilter === 'all' ? undefined : stateFilter
      const [projectsList, treeData, teamsList] = await Promise.all([
        listProjects({ state, include_teams: true }),
        getProjectTree({ state }),
        listTeams(),
      ])
      // Ensure we always have arrays (API might return error objects)
      setProjects(Array.isArray(projectsList) ? projectsList : [])
      setTree(Array.isArray(treeData) ? treeData : [])
      setAvailableTeams(Array.isArray(teamsList) ? teamsList : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load projects')
    } finally {
      setLoading(false)
    }
  }, [listProjects, getProjectTree, listTeams, stateFilter])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleCreate = async (data: ProjectCreate) => {
    await createProject(data)
    setShowCreateModal(false)
    loadData()
  }

  const handleUpdate = async (id: number, data: ProjectUpdate) => {
    await updateProject(id, data)
    setEditingProject(null)
    loadData()
  }

  const handleDelete = async (id: number) => {
    await deleteProject(id)
    setDeletingProject(null)
    loadData()
  }

  // Check if there's any tree hierarchy (any project has children)
  const hasHierarchy = tree.some(node => node.children.length > 0)
  // Always show toggle if currently in tree mode (so user can switch back)
  const showViewToggle = hasHierarchy || viewMode === 'tree'

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Projects</h3>
        <div className="flex items-center gap-2">
          {/* State filter */}
          <select
            value={stateFilter}
            onChange={e => setStateFilter(e.target.value as 'all' | 'open' | 'closed')}
            className={cx(styles.formSelect, 'w-auto py-1.5')}
          >
            <option value="all">All</option>
            <option value="open">Open</option>
            <option value="closed">Closed</option>
          </select>
          {/* View toggle - show if there's hierarchy or user is in tree mode */}
          {showViewToggle && (
            <div className="inline-flex border border-slate-200 rounded-lg">
              <button
                type="button"
                className={cx(
                  'px-3 py-1.5 text-sm cursor-pointer transition-colors rounded-l-lg',
                  viewMode === 'tree'
                    ? 'bg-primary text-white hover:bg-primary-dark'
                    : 'bg-white text-slate-600 hover:bg-slate-50'
                )}
                onClick={() => setViewMode('tree')}
              >
                Tree
              </button>
              <button
                type="button"
                className={cx(
                  'px-3 py-1.5 text-sm cursor-pointer transition-colors border-l border-slate-200 rounded-r-lg',
                  viewMode === 'list'
                    ? 'bg-primary text-white hover:bg-primary-dark'
                    : 'bg-white text-slate-600 hover:bg-slate-50'
                )}
                onClick={() => setViewMode('list')}
              >
                List
              </button>
            </div>
          )}
          <button className={styles.btnAdd} onClick={() => setShowCreateModal(true)}>
            New Project
          </button>
        </div>
      </div>

      {projects.length === 0 ? (
        <EmptyState
          message="No projects found. Create a standalone project or sync GitHub milestones."
          actionLabel="Create Project"
          onAction={() => setShowCreateModal(true)}
        />
      ) : viewMode === 'tree' && hasHierarchy ? (
        <div className={styles.sourceList}>
          <ProjectTree
            nodes={tree}
            onEdit={setEditingProject}
            onDelete={setDeletingProject}
            onManageTeams={setManagingTeams}
            projects={projects}
          />
        </div>
      ) : (
        <div className={styles.sourceList}>
          {projects.map(project => (
            <ProjectCard
              key={project.id}
              project={project}
              onEdit={() => setEditingProject(project)}
              onDelete={() => setDeletingProject(project)}
              onManageTeams={() => setManagingTeams(project)}
            />
          ))}
        </div>
      )}

      {showCreateModal && (
        <ProjectFormModal
          title="Create Project"
          projects={projects}
          onSubmit={handleCreate}
          onClose={() => setShowCreateModal(false)}
          availableTeams={availableTeams}
        />
      )}

      {editingProject && (
        <ProjectFormModal
          title="Edit Project"
          project={editingProject}
          projects={projects}
          onSubmit={data => handleUpdate(editingProject.id, data)}
          onClose={() => setEditingProject(null)}
        />
      )}

      {deletingProject && (
        deletingProject.repo_path ? (
          <Modal title="Cannot Delete" onClose={() => setDeletingProject(null)}>
            <p className="text-slate-600 mb-4">
              GitHub-backed projects cannot be deleted. Close them in GitHub instead.
            </p>
            <div className={styles.formActions}>
              <button className={styles.btnCancel} onClick={() => setDeletingProject(null)}>
                OK
              </button>
            </div>
          </Modal>
        ) : (
          <ConfirmDialog
            message={`Are you sure you want to delete "${deletingProject.title}"? ${
              deletingProject.children_count > 0
                ? `Its ${deletingProject.children_count} child project(s) will become top-level.`
                : ''
            }`}
            onConfirm={() => handleDelete(deletingProject.id)}
            onCancel={() => setDeletingProject(null)}
          />
        )
      )}

      {managingTeams && (
        <ProjectTeamsModal
          project={managingTeams}
          onClose={() => {
            setManagingTeams(null)
            loadData()  // Refresh to show updated team assignments
          }}
          listTeams={listTeams}
          listProjectTeams={listProjectTeams}
          assignTeam={assignTeamToProject}
          unassignTeam={unassignTeamFromProject}
        />
      )}
    </div>
  )
}

// Tree view component
interface ProjectTreeProps {
  nodes: ProjectTreeNode[]
  onEdit: (project: Project) => void
  onDelete: (project: Project) => void
  onManageTeams: (project: Project) => void
  projects: Project[]
  depth?: number
}

const ProjectTree = ({ nodes, onEdit, onDelete, onManageTeams, projects, depth = 0 }: ProjectTreeProps) => {
  if (nodes.length === 0) return null

  return (
    <div className={depth > 0 ? 'ml-6 border-l border-slate-200 pl-4' : ''}>
      {nodes.map(node => {
        const project = projects.find(p => p.id === node.id)
        return (
          <div key={node.id} className="mb-2">
            <ProjectCard
              project={project || {
                id: node.id,
                title: node.title,
                description: node.description,
                state: node.state as 'open' | 'closed',
                repo_path: node.repo_path,
                github_id: null,
                number: null,
                parent_id: node.parent_id,
                children_count: node.children.length,
              }}
              onEdit={() => project && onEdit(project)}
              onDelete={() => project && onDelete(project)}
              onManageTeams={() => project && onManageTeams(project)}
              compact
            />
            {node.children.length > 0 && (
              <ProjectTree
                nodes={node.children}
                onEdit={onEdit}
                onDelete={onDelete}
                onManageTeams={onManageTeams}
                projects={projects}
                depth={depth + 1}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

// Project card component
interface ProjectCardProps {
  project: Project
  onEdit: () => void
  onDelete: () => void
  onManageTeams: () => void
  compact?: boolean
}

const ProjectCard = ({ project, onEdit, onDelete, onManageTeams, compact }: ProjectCardProps) => {
  const isGithubBacked = project.repo_path !== null
  const isOpen = project.state === 'open'

  return (
    <div
      className={cx(
        styles.card,
        !isOpen && 'opacity-60',
        compact && 'p-3'
      )}
    >
      <div className={styles.cardHeader}>
        <div className={styles.cardInfo}>
          <div className="flex items-center gap-2">
            <h4 className={cx(styles.cardTitle, compact && 'text-sm')}>
              {project.title}
            </h4>
            {/* Badges */}
            <span
              className={cx(
                styles.badge,
                isOpen ? styles.badgeActive : styles.badgeInactive
              )}
            >
              {project.state}
            </span>
            {isGithubBacked && (
              <span className={cx(styles.badge, 'bg-purple-100 text-purple-700')}>
                GitHub
              </span>
            )}
          </div>
          {project.description && !compact && (
            <p className="text-sm text-slate-500 mt-1 line-clamp-2">
              {project.description}
            </p>
          )}
          {isGithubBacked && (
            <p className="text-xs text-slate-400 mt-1">
              {project.repo_path} #{project.number}
            </p>
          )}
          {project.teams && project.teams.length > 0 && !compact && (
            <div className="flex items-center gap-1.5 mt-2 flex-wrap">
              <span className="text-xs text-slate-500">Teams:</span>
              {project.teams.slice(0, 3).map(team => (
                <span
                  key={team.id}
                  className="bg-green-50 text-green-700 px-2 py-0.5 rounded text-xs cursor-pointer hover:bg-green-100"
                  onClick={onManageTeams}
                >
                  {team.name}
                </span>
              ))}
              {project.teams.length > 3 && (
                <span
                  className="text-xs text-slate-500 cursor-pointer hover:text-slate-700"
                  onClick={onManageTeams}
                  title={project.teams.slice(3).map(t => t.name).join(', ')}
                >
                  +{project.teams.length - 3} more
                </span>
              )}
            </div>
          )}
        </div>
        <div className={styles.cardActions}>
          {project.children_count > 0 && (
            <span className="text-xs text-slate-500">
              {project.children_count} child{project.children_count !== 1 ? 'ren' : ''}
            </span>
          )}
          <button className={styles.btnEdit} onClick={onManageTeams}>
            Teams
          </button>
          <button className={styles.btnEdit} onClick={onEdit}>
            Edit
          </button>
          <button
            className={cx(styles.btnDelete, isGithubBacked && 'opacity-50 cursor-not-allowed')}
            onClick={onDelete}
            title={isGithubBacked ? 'GitHub-backed projects cannot be deleted' : undefined}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

// Form modal for create/edit
interface ProjectFormModalProps {
  title: string
  project?: Project
  projects: Project[]
  onSubmit: (data: ProjectCreate | ProjectUpdate) => Promise<void>
  onClose: () => void
  availableTeams?: Team[]  // Required for create, used for team selection
}

const ProjectFormModal = ({
  title,
  project,
  projects,
  onSubmit,
  onClose,
  availableTeams = [],
}: ProjectFormModalProps) => {
  const isEditing = !!project
  const isGithubBacked = project?.repo_path !== null

  const [formData, setFormData] = useState({
    title: project?.title || '',
    description: project?.description || '',
    state: project?.state || 'open' as 'open' | 'closed',
    parent_id: project?.parent_id || null as number | null,
    team_ids: [] as number[],  // For new projects
  })

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Get valid parent options (exclude self and descendants)
  const getValidParents = () => {
    if (!isEditing) {
      return projects
    }

    // Build set of descendants to exclude
    const descendants = new Set<number>()
    const findDescendants = (parentId: number) => {
      projects.forEach(p => {
        if (p.parent_id === parentId && !descendants.has(p.id)) {
          descendants.add(p.id)
          findDescendants(p.id)
        }
      })
    }
    findDescendants(project.id)

    return projects.filter(p => p.id !== project.id && !descendants.has(p.id))
  }

  const validParents = getValidParents()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    // Validate team selection for new projects
    if (!isEditing && formData.team_ids.length === 0) {
      setError('Please select at least one team')
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      const data: ProjectCreate | ProjectUpdate = isEditing
        ? {
            // For GitHub-backed, only allow parent_id changes
            ...(isGithubBacked
              ? { parent_id: formData.parent_id }
              : {
                  title: formData.title || undefined,
                  description: formData.description || null,
                  state: formData.state,
                  parent_id: formData.parent_id,
                }),
          }
        : {
            title: formData.title,
            team_ids: formData.team_ids,
            description: formData.description || null,
            state: formData.state,
            parent_id: formData.parent_id,
          }

      await onSubmit(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save project')
      setSubmitting(false)
    }
  }

  const handleTeamToggle = (teamId: number) => {
    setFormData(prev => ({
      ...prev,
      team_ids: prev.team_ids.includes(teamId)
        ? prev.team_ids.filter(id => id !== teamId)
        : [...prev.team_ids, teamId],
    }))
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        {isEditing && isGithubBacked && (
          <div className="p-3 bg-amber-50 border border-amber-200 text-amber-800 rounded-lg text-sm mb-4">
            This project is synced from GitHub. Only parent can be changed here.
            Edit title/description/state in GitHub.
          </div>
        )}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Title</label>
          <input
            type="text"
            value={formData.title}
            onChange={e => setFormData({ ...formData, title: e.target.value })}
            required={!isEditing}
            disabled={isEditing && isGithubBacked}
            placeholder="Project name"
            className={styles.formInput}
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Description</label>
          <textarea
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
            disabled={isEditing && isGithubBacked}
            placeholder="Optional description"
            rows={3}
            className={styles.formTextarea}
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>State</label>
          <select
            value={formData.state}
            onChange={e => setFormData({ ...formData, state: e.target.value as 'open' | 'closed' })}
            disabled={isEditing && isGithubBacked}
            className={styles.formSelect}
          >
            <option value="open">Open</option>
            <option value="closed">Closed</option>
          </select>
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Parent Project</label>
          <select
            value={formData.parent_id ?? ''}
            onChange={e => setFormData({
              ...formData,
              parent_id: e.target.value ? Number(e.target.value) : null,
            })}
            className={styles.formSelect}
          >
            <option value="">None (top-level)</option>
            {validParents.map(p => (
              <option key={p.id} value={p.id}>
                {p.title}
                {p.repo_path && ` (${p.repo_path})`}
              </option>
            ))}
          </select>
          <p className={styles.formHint}>
            Organize projects into a hierarchy by setting a parent.
          </p>
        </div>

        {/* Team selection - only for new projects */}
        {!isEditing && (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>
              Teams <span className="text-red-500">*</span>
            </label>
            <p className="text-sm text-slate-500 mb-2">
              Select which teams will have access to this project.
            </p>
            {availableTeams.length === 0 ? (
              <p className="text-sm text-amber-600 bg-amber-50 p-3 rounded-lg">
                No teams available. Create a team first before creating a project.
              </p>
            ) : (
              <div className="space-y-2 max-h-48 overflow-y-auto border border-slate-200 rounded-lg p-2">
                {availableTeams.map(team => (
                  <label
                    key={team.id}
                    className={cx(
                      'flex items-center gap-2 p-2 rounded cursor-pointer transition-colors',
                      formData.team_ids.includes(team.id)
                        ? 'bg-purple-50 border border-purple-200'
                        : 'hover:bg-slate-50'
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={formData.team_ids.includes(team.id)}
                      onChange={() => handleTeamToggle(team.id)}
                      className="w-4 h-4 text-purple-600 rounded border-slate-300 focus:ring-purple-500"
                    />
                    <span className="font-medium text-slate-900">{team.name}</span>
                    <span className="text-sm text-slate-500">@{team.slug}</span>
                  </label>
                ))}
              </div>
            )}
            {formData.team_ids.length > 0 && (
              <p className={styles.formHint}>
                {formData.team_ids.length} team{formData.team_ids.length !== 1 ? 's' : ''} selected
              </p>
            )}
          </div>
        )}

        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button
            type="submit"
            className={styles.btnSubmit}
            disabled={submitting || (!isEditing && availableTeams.length === 0)}
          >
            {submitting ? 'Saving...' : isEditing ? 'Update' : 'Create'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// Project Teams Management Modal
interface ProjectTeamsModalProps {
  project: Project
  onClose: () => void
  listTeams: () => Promise<Team[]>
  listProjectTeams: (project: number) => Promise<Team[]>
  assignTeam: (project: number, teamId: number) => Promise<{ success: boolean; error?: string }>
  unassignTeam: (project: number, teamId: number) => Promise<{ success: boolean; error?: string }>
}

const ProjectTeamsModal = ({
  project,
  onClose,
  listTeams,
  listProjectTeams,
  assignTeam,
  unassignTeam,
}: ProjectTeamsModalProps) => {
  const [assignedTeams, setAssignedTeams] = useState<Team[]>([])
  const [allTeams, setAllTeams] = useState<Team[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Load teams on mount
  useEffect(() => {
    const loadData = async () => {
      setLoading(true)
      try {
        const [assigned, all] = await Promise.all([
          listProjectTeams(project.id),
          listTeams(),
        ])
        setAssignedTeams(assigned)
        setAllTeams(all)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load teams')
      } finally {
        setLoading(false)
      }
    }
    loadData()
  }, [project.id, listProjectTeams, listTeams])

  const handleAssign = async (team: Team) => {
    setError(null)
    const result = await assignTeam(project.id, team.id)
    if (result.success) {
      setAssignedTeams(prev => [...prev, team])
    } else {
      setError(result.error || 'Failed to assign team')
    }
  }

  const handleUnassign = async (team: Team) => {
    setError(null)
    const result = await unassignTeam(project.id, team.id)
    if (result.success) {
      setAssignedTeams(prev => prev.filter(t => t.id !== team.id))
    } else {
      setError(result.error || 'Failed to unassign team')
    }
  }

  const assignedIds = new Set(assignedTeams.map(t => t.id))
  const availableTeams = allTeams.filter(t => !assignedIds.has(t.id))

  return (
    <Modal title={`Teams for ${project.title}`} onClose={onClose}>
      <div className="space-y-4">
        {error && <div className={styles.formError}>{error}</div>}

        {loading ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : (
          <>
            {/* Assigned teams */}
            <div>
              <h4 className="text-sm font-medium text-slate-700 mb-2">
                Assigned Teams ({assignedTeams.length})
              </h4>
              {assignedTeams.length === 0 ? (
                <p className="text-sm text-slate-500">No teams assigned</p>
              ) : (
                <div className="space-y-2">
                  {assignedTeams.map(team => (
                    <div
                      key={team.id}
                      className="flex items-center justify-between py-2 px-3 bg-purple-50 rounded-lg"
                    >
                      <div>
                        <span className="font-medium text-purple-900">{team.name}</span>
                        <span className="text-purple-600 text-sm ml-2">@{team.slug}</span>
                        {team.member_count !== undefined && (
                          <span className="text-purple-500 text-xs ml-2">
                            ({team.member_count} member{team.member_count !== 1 ? 's' : ''})
                          </span>
                        )}
                      </div>
                      <button
                        type="button"
                        className="text-red-600 text-sm hover:text-red-700"
                        onClick={() => handleUnassign(team)}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Available teams to add */}
            {availableTeams.length > 0 && (
              <div>
                <h4 className="text-sm font-medium text-slate-700 mb-2">
                  Available Teams
                </h4>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {availableTeams.map(team => (
                    <div
                      key={team.id}
                      className="flex items-center justify-between py-2 px-3 bg-slate-50 rounded-lg"
                    >
                      <div>
                        <span className="font-medium">{team.name}</span>
                        <span className="text-slate-500 text-sm ml-2">@{team.slug}</span>
                        {team.member_count !== undefined && (
                          <span className="text-slate-400 text-xs ml-2">
                            ({team.member_count} member{team.member_count !== 1 ? 's' : ''})
                          </span>
                        )}
                      </div>
                      <button
                        type="button"
                        className="text-primary text-sm hover:text-primary/80"
                        onClick={() => handleAssign(team)}
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

export default ProjectsPanel
