import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useProjects, Project, ProjectTreeNode, ProjectCreate, ProjectUpdate, Collaborator, CollaboratorInput } from '@/hooks/useProjects'
import { usePeople, Person } from '@/hooks/usePeople'
import {
  Modal,
  EmptyState,
  LoadingState,
  ErrorState,
  ConfirmDialog,
} from '../shared'
import { styles, cx } from '../styles'

type CollaboratorRole = 'contributor' | 'manager' | 'admin'

export const ProjectsPanel = () => {
  const {
    listProjects,
    getProjectTree,
    createProject,
    updateProject,
    deleteProject,
  } = useProjects()

  const [projects, setProjects] = useState<Project[]>([])
  const [tree, setTree] = useState<ProjectTreeNode[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'tree' | 'list'>('list')
  const [stateFilter, setStateFilter] = useState<'all' | 'open' | 'closed'>('all')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [deletingProject, setDeletingProject] = useState<Project | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const state = stateFilter === 'all' ? undefined : stateFilter
      const [projectsList, treeData] = await Promise.all([
        listProjects({ state, include_children: true }),
        getProjectTree({ state }),
      ])
      // Ensure we always have arrays (API might return error objects)
      setProjects(Array.isArray(projectsList) ? projectsList : [])
      setTree(Array.isArray(treeData) ? treeData : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load projects')
    } finally {
      setLoading(false)
    }
  }, [listProjects, getProjectTree, stateFilter])

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
          {/* View toggle - only show if there's a hierarchy */}
          {hasHierarchy && (
            <div className="flex border border-slate-200 rounded-lg overflow-hidden">
              <button
                type="button"
                className={cx(
                  'px-3 py-1.5 text-sm cursor-pointer transition-colors',
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
                  'px-3 py-1.5 text-sm border-l border-slate-200 cursor-pointer transition-colors',
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
    </div>
  )
}

// Tree view component
interface ProjectTreeProps {
  nodes: ProjectTreeNode[]
  onEdit: (project: Project) => void
  onDelete: (project: Project) => void
  projects: Project[]
  depth?: number
}

const ProjectTree = ({ nodes, onEdit, onDelete, projects, depth = 0 }: ProjectTreeProps) => {
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
                collaborators: [],
              }}
              onEdit={() => project && onEdit(project)}
              onDelete={() => project && onDelete(project)}
              compact
            />
            {node.children.length > 0 && (
              <ProjectTree
                nodes={node.children}
                onEdit={onEdit}
                onDelete={onDelete}
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
  compact?: boolean
}

const ProjectCard = ({ project, onEdit, onDelete, compact }: ProjectCardProps) => {
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
          {project.collaborators && project.collaborators.length > 0 && !compact && (
            <div className="flex items-center gap-1 mt-1 flex-wrap">
              <span className="text-xs text-slate-400">Collaborators:</span>
              {project.collaborators.slice(0, 3).map(c => (
                <span key={c.person_id} className="text-xs bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
                  {c.display_name}
                </span>
              ))}
              {project.collaborators.length > 3 && (
                <span className="text-xs text-slate-400">
                  +{project.collaborators.length - 3} more
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
}

const ProjectFormModal = ({
  title,
  project,
  projects,
  onSubmit,
  onClose,
}: ProjectFormModalProps) => {
  const isEditing = !!project
  const isGithubBacked = project?.repo_path !== null
  const { listPeople } = usePeople()

  const [formData, setFormData] = useState({
    title: project?.title || '',
    description: project?.description || '',
    state: project?.state || 'open' as 'open' | 'closed',
    parent_id: project?.parent_id || null as number | null,
  })

  // Collaborator state
  const [collaborators, setCollaborators] = useState<Array<{
    person_id: number
    display_name: string
    role: CollaboratorRole
  }>>(
    project?.collaborators?.map(c => ({
      person_id: c.person_id,
      display_name: c.display_name,
      role: c.role as CollaboratorRole,
    })) || []
  )
  const [peopleSearch, setPeopleSearch] = useState('')
  const [peopleResults, setPeopleResults] = useState<Person[]>([])
  const [showPeopleDropdown, setShowPeopleDropdown] = useState(false)
  const [searchingPeople, setSearchingPeople] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Use ref for listPeople to avoid effect re-running when function reference changes
  const listPeopleRef = useRef(listPeople)
  listPeopleRef.current = listPeople

  // Search for people when search term changes
  useEffect(() => {
    if (peopleSearch.length < 2) {
      setPeopleResults([])
      return
    }

    setSearchingPeople(true)
    const controller = new AbortController()

    const searchPeople = async () => {
      try {
        const results = await listPeopleRef.current({ search: peopleSearch, limit: 10 })
        if (controller.signal.aborted) return
        setPeopleResults(results)
      } catch {
        if (!controller.signal.aborted) {
          setPeopleResults([])
        }
      } finally {
        if (!controller.signal.aborted) {
          setSearchingPeople(false)
        }
      }
    }

    const debounce = setTimeout(searchPeople, 300)
    return () => {
      clearTimeout(debounce)
      controller.abort()
    }
  }, [peopleSearch])

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowPeopleDropdown(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const addCollaborator = (person: Person) => {
    setCollaborators(prev => [...prev, {
      person_id: person.id,
      display_name: person.display_name,
      role: 'contributor' as CollaboratorRole,
    }])
    setPeopleSearch('')
    setShowPeopleDropdown(false)
  }

  const removeCollaborator = (personId: number) => {
    setCollaborators(prev => prev.filter(c => c.person_id !== personId))
  }

  const updateCollaboratorRole = (personId: number, role: CollaboratorRole) => {
    setCollaborators(prev => prev.map(c =>
      c.person_id === personId ? { ...c, role } : c
    ))
  }

  // Filter out already-selected collaborators from search results
  const filteredPeopleResults = useMemo(() => {
    const selectedIds = new Set(collaborators.map(c => c.person_id))
    return peopleResults.filter(p => !selectedIds.has(p.id))
  }, [peopleResults, collaborators])

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
    setSubmitting(true)
    setError(null)

    try {
      // Convert collaborators to API format
      const collaboratorInputs: CollaboratorInput[] = collaborators.map(c => ({
        person_id: c.person_id,
        role: c.role,
      }))

      const data: ProjectCreate | ProjectUpdate = isEditing
        ? {
            // For GitHub-backed, only allow parent_id and collaborator changes
            ...(isGithubBacked
              ? { parent_id: formData.parent_id, collaborators: collaboratorInputs }
              : {
                  title: formData.title || undefined,
                  description: formData.description || null,
                  state: formData.state,
                  parent_id: formData.parent_id,
                  collaborators: collaboratorInputs,
                }),
          }
        : {
            title: formData.title,
            description: formData.description || null,
            state: formData.state,
            parent_id: formData.parent_id,
            collaborators: collaboratorInputs,
          }

      await onSubmit(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save project')
      setSubmitting(false)
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        {isEditing && isGithubBacked && (
          <div className="p-3 bg-amber-50 border border-amber-200 text-amber-800 rounded-lg text-sm mb-4">
            This project is synced from GitHub. Only parent and collaborators can be changed here.
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

        {/* Collaborators Section */}
        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Collaborators</label>

          {/* Search input */}
          <div className="relative" ref={dropdownRef}>
            <input
              type="text"
              value={peopleSearch}
              onChange={e => {
                setPeopleSearch(e.target.value)
                setShowPeopleDropdown(true)
              }}
              onFocus={() => setShowPeopleDropdown(true)}
              placeholder="Search people to add..."
              className={styles.formInput}
            />

            {/* Search results dropdown */}
            {showPeopleDropdown && (filteredPeopleResults.length > 0 || searchingPeople) && (
              <div className="absolute z-10 w-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-48 overflow-auto">
                {searchingPeople ? (
                  <div className="px-4 py-2 text-sm text-slate-500">Searching...</div>
                ) : (
                  filteredPeopleResults.map(person => (
                    <button
                      key={person.id}
                      type="button"
                      className="w-full px-4 py-2 text-left text-sm hover:bg-slate-50 flex items-center justify-between cursor-pointer"
                      onClick={() => addCollaborator(person)}
                    >
                      <span className="font-medium">{person.display_name}</span>
                      <span className="text-slate-400 text-xs">{person.identifier}</span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>

          {/* Selected collaborators */}
          {collaborators.length > 0 && (
            <div className="mt-3 space-y-2">
              {collaborators.map(collab => (
                <div
                  key={collab.person_id}
                  className="flex items-center gap-2 p-2 bg-slate-50 rounded-lg"
                >
                  <span className="flex-1 text-sm font-medium">{collab.display_name}</span>
                  <select
                    value={collab.role}
                    onChange={e => updateCollaboratorRole(collab.person_id, e.target.value as CollaboratorRole)}
                    className="text-xs border border-slate-200 rounded px-2 py-1 bg-white"
                  >
                    <option value="contributor">Contributor</option>
                    <option value="manager">Manager</option>
                    <option value="admin">Admin</option>
                  </select>
                  <button
                    type="button"
                    onClick={() => removeCollaborator(collab.person_id)}
                    className="text-slate-400 hover:text-red-500 cursor-pointer"
                    title="Remove collaborator"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}

          <p className={styles.formHint}>
            Add people who can access this project. Type to search.
          </p>
        </div>

        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button type="submit" className={styles.btnSubmit} disabled={submitting}>
            {submitting ? 'Saving...' : isEditing ? 'Update' : 'Create'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default ProjectsPanel
