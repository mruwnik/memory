import { useState, useEffect, useCallback } from 'react'
import { useProjects, Project, ProjectTreeNode, ProjectCreate, ProjectUpdate, JournalEntry } from '@/hooks/useProjects'
import { useTeams, Team } from '@/hooks/useTeams'
import { useJournal } from '@/hooks/useJournal'
import { usePeople } from '@/hooks/usePeople'
import { PersonSelect, type ListPeople } from '@/components/shared/PersonPicker'
import { Badge, Button, ConfirmButton, cx, EmptyState, ExpandToggle, Field, FormActions, Input, List, LoadingState, Modal, MultiSelect, Notice, Page, Row, RowError, Select, SubList, Textarea, ToggleGroup, Toolbar, useActionNotice } from '@/components/shared/ui'
import { formatUserDateTime } from '@/utils/timezones'


export const ProjectsPanel = () => {
  const {
    listProjects,
    getProjectTree,
    getProject,
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
  const { listPeople } = usePeople()

  const { addJournalEntry } = useJournal()
  const { run, notice } = useActionNotice()

  const [projects, setProjects] = useState<Project[]>([])
  const [tree, setTree] = useState<ProjectTreeNode[]>([])
  const [availableTeams, setAvailableTeams] = useState<Team[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'tree' | 'list'>(() => {
    const stored = localStorage.getItem('projects-view-mode')
    return stored === 'list' || stored === 'tree' ? stored : 'tree'
  })
  const [stateFilter, setStateFilter] = useState<'all' | 'open' | 'closed'>('open')
  const [searchQuery, setSearchQuery] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [managingTeams, setManagingTeams] = useState<Project | null>(null)
  const [collapsedNodes, setCollapsedNodes] = useState<Set<number>>(() => {
    const saved = localStorage.getItem('projects-collapsed-nodes')
    if (saved) {
      try {
        const parsed = JSON.parse(saved)
        if (Array.isArray(parsed)) {
          return new Set(parsed)
        }
      } catch {
        // Ignore parse errors
      }
    }
    return new Set()
  })

  const toggleCollapse = useCallback((nodeId: number) => {
    setCollapsedNodes(prev => {
      const next = new Set(prev)
      if (next.has(nodeId)) {
        next.delete(nodeId)
      } else {
        next.add(nodeId)
      }
      return next
    })
  }, [])

  // Persist collapsed state to localStorage
  useEffect(() => {
    localStorage.setItem('projects-collapsed-nodes', JSON.stringify(Array.from(collapsedNodes)))
  }, [collapsedNodes])

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const state = stateFilter === 'all' ? undefined : stateFilter
      const [projectsList, treeData, teamsList] = await Promise.all([
        listProjects({ state, include_teams: true, limit: 500 }),
        getProjectTree({ state }),
        listTeams(),
      ])
      setProjects(projectsList)
      setTree(treeData)
      setAvailableTeams(teamsList)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load projects')
    } finally {
      setLoading(false)
    }
  }, [listProjects, getProjectTree, listTeams, stateFilter])

  useEffect(() => {
    loadData()
  }, [loadData])

  // Persist view mode preference
  useEffect(() => {
    localStorage.setItem('projects-view-mode', viewMode)
  }, [viewMode])

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

  const handleDelete = (project: Project) => run(async () => {
    await deleteProject(project.id)
    await loadData()
  }, `Deleted ${project.title}`)

  // Check if there's any tree hierarchy (any project has children)
  const hasHierarchy = tree.some(node => node.children.length > 0)
  // Always show toggle if currently in tree mode (so user can switch back)
  const showViewToggle = hasHierarchy || viewMode === 'tree'

  // Filter projects by search query (for list view)
  const filteredProjects = searchQuery.trim()
    ? projects.filter(p =>
        p.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.repo_path?.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : projects

  // Filter tree nodes recursively (for tree view)
  const filterTreeNodes = (nodes: ProjectTreeNode[]): ProjectTreeNode[] => {
    if (!searchQuery.trim()) return nodes
    const query = searchQuery.toLowerCase()

    const filterNode = (node: ProjectTreeNode): ProjectTreeNode | null => {
      const matchesSelf =
        node.title.toLowerCase().includes(query) ||
        node.description?.toLowerCase().includes(query) ||
        node.repo_path?.toLowerCase().includes(query)

      const filteredChildren = node.children
        .map(filterNode)
        .filter((n): n is ProjectTreeNode => n !== null)

      // Include node if it matches or has matching children
      if (matchesSelf || filteredChildren.length > 0) {
        return { ...node, children: filteredChildren }
      }
      return null
    }

    return nodes.map(filterNode).filter((n): n is ProjectTreeNode => n !== null)
  }

  const filteredTree = filterTreeNodes(tree)

  return (
    <Page
      title="Projects"
      actions={<Button variant="primary" onClick={() => setShowCreateModal(true)}>New project</Button>}
      width="wide"
      loading={loading}
      error={error}
      onRetry={loadData}
    >
      <Toolbar>
        <Input type="search" placeholder="Search projects..." aria-label="Search projects" value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)} className="sm:w-64" />
        <Select value={stateFilter} onChange={e => setStateFilter(e.target.value as 'all' | 'open' | 'closed')} className="w-auto" aria-label="State">
          <option value="all">All</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
        </Select>
        {showViewToggle && (
          <ToggleGroup label="View mode" value={viewMode} onChange={setViewMode}
            options={[{ value: 'tree', label: 'Tree' }, { value: 'list', label: 'List' }]} />
        )}
      </Toolbar>

      {notice}

      {projects.length === 0 ? (
        <EmptyState
          message="No projects found. Create a standalone project or sync GitHub milestones."
          actionLabel="New project"
          onAction={() => setShowCreateModal(true)}
        />
      ) : viewMode === 'tree' && hasHierarchy ? (
        filteredTree.length === 0 && searchQuery ? (
          <EmptyState message={`No projects match "${searchQuery}"`} />
        ) : (
          <List>
            <ProjectTree
              nodes={filteredTree}
              onEdit={setEditingProject}
              onDelete={handleDelete}
              onManageTeams={setManagingTeams}
              projects={projects}
              collapsedNodes={collapsedNodes}
              onToggleCollapse={toggleCollapse}
              getProject={getProject}
              addJournalEntry={addJournalEntry}
            />
          </List>
        )
      ) : filteredProjects.length === 0 && searchQuery ? (
        <EmptyState message={`No projects match "${searchQuery}"`} />
      ) : (
        <List>
          {filteredProjects.map(project => {
              // For list view, compute inherited owner from parent chain
              const getInheritedOwner = (p: Project): typeof project.owner => {
                if (p.owner) return p.owner
                if (p.parent_id) {
                  const parent = projects.find(pr => pr.id === p.parent_id)
                  if (parent) return getInheritedOwner(parent)
                }
                return null
              }
              const inheritedOwner = project.owner ? null : getInheritedOwner(project)
              return (
                <ProjectRow
                  key={project.id}
                  project={project}
                  onEdit={() => setEditingProject(project)}
                  onDelete={() => handleDelete(project)}
                  onManageTeams={() => setManagingTeams(project)}
                  inheritedOwner={inheritedOwner}
                  getProject={getProject}
                  addJournalEntry={addJournalEntry}
                />
              )
            })}
        </List>
      )}

      {showCreateModal && (
        <ProjectFormModal
          title="New project"
          projects={projects}
          onSubmit={handleCreate}
          onClose={() => setShowCreateModal(false)}
          availableTeams={availableTeams}
          listPeople={listPeople}
        />
      )}

      {editingProject && (
        <ProjectFormModal
          title="Edit project"
          project={editingProject}
          projects={projects}
          onSubmit={data => handleUpdate(editingProject.id, data)}
          onClose={() => setEditingProject(null)}
          listPeople={listPeople}
        />
      )}

      {managingTeams && (
        <ProjectTeamsModal
          project={managingTeams}
          onClose={(hasChanges) => {
            setManagingTeams(null)
            if (hasChanges) {
              loadData()  // Refresh only if team assignments changed
            }
          }}
          listTeams={listTeams}
          listProjectTeams={listProjectTeams}
          assignTeam={assignTeamToProject}
          unassignTeam={unassignTeamFromProject}
        />
      )}
    </Page>
  )
}

// Tree view component
interface ProjectTreeProps {
  nodes: ProjectTreeNode[]
  onEdit: (project: Project) => void
  onDelete: (project: Project) => void
  onManageTeams: (project: Project) => void
  projects: Project[]
  collapsedNodes: Set<number>
  onToggleCollapse: (nodeId: number) => void
  inheritedOwner?: { id: number; display_name: string | null; identifier: string } | null
  depth?: number
  getProject: (id: number, options: { includeJournal?: boolean }) => Promise<{ project: Project | null; journal_entries?: JournalEntry[] }>
  addJournalEntry: (targetId: number, content: string, targetType: 'project') => Promise<{ status: string; entry: JournalEntry }>
}

const ProjectTree = ({ nodes, onEdit, onDelete, onManageTeams, projects, collapsedNodes, onToggleCollapse, inheritedOwner, depth = 0, getProject, addJournalEntry }: ProjectTreeProps) => {
  if (nodes.length === 0) return null

  const rows = nodes.map(node => {
    // Try to find full project data, fall back to node data
    const project = projects.find(p => p.id === node.id)
    const projectData: Project = project || {
      id: node.id,
      title: node.title,
      description: node.description,
      state: node.state as 'open' | 'closed',
      repo_path: node.repo_path,
      github_id: null,
      number: null,
      parent_id: node.parent_id,
      children_count: node.children.length,
      owner_id: null,
      due_on: null,
      doc_url: node.doc_url,
    }
    const hasChildren = node.children.length > 0
    const isCollapsed = collapsedNodes.has(node.id)
    // Children inherit this node's owner, or whatever this node inherited
    const ownerToPassDown = projectData.owner || inheritedOwner

    return (
      <ProjectRow
        key={node.id}
        project={projectData}
        onEdit={() => onEdit(projectData)}
        onDelete={() => onDelete(projectData)}
        onManageTeams={() => onManageTeams(projectData)}
        inheritedOwner={inheritedOwner}
        compact
        getProject={getProject}
        addJournalEntry={addJournalEntry}
        leading={hasChildren && (
          <Button variant="ghost" size="icon" className="w-7 h-7 text-base" onClick={() => onToggleCollapse(node.id)}
            aria-expanded={!isCollapsed} aria-label={isCollapsed ? `Expand ${projectData.title}` : `Collapse ${projectData.title}`}>
            <span aria-hidden="true" className={cx('inline-block transition-transform motion-reduce:transition-none', !isCollapsed && 'rotate-90')}>▸</span>
          </Button>
        )}
      >
        {hasChildren && !isCollapsed && (
          <List flush className="border-l border-slate-200 [&>li]:pr-0">
            <ProjectTree
              nodes={node.children}
              onEdit={onEdit}
              onDelete={onDelete}
              onManageTeams={onManageTeams}
              projects={projects}
              collapsedNodes={collapsedNodes}
              onToggleCollapse={onToggleCollapse}
              inheritedOwner={ownerToPassDown}
              depth={depth + 1}
              getProject={getProject}
              addJournalEntry={addJournalEntry}
            />
          </List>
        )}
      </ProjectRow>
    )
  })
  return <>{rows}</>
}

// due_on is a calendar date stored as midnight UTC (or GitHub's midnight Pacific), so only its date
// part means anything: read it as a local date to show the same day in every timezone.
const dueDay = (dueOn: string) => {
  const [y, m, d] = dueOn.slice(0, 10).split('-').map(Number)
  return new Date(y, m - 1, d)
}

const daysUntilDue = (dueOn: string) => {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((dueDay(dueOn).getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

const formatDueDate = (dueOn: string) =>
  dueDay(dueOn).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })

const DueBadge = ({ dueOn }: { dueOn: string }) => {
  const days = daysUntilDue(dueOn)
  if (days > 21) return null
  const label = days < 0 ? 'Overdue' : days < 7 ? 'Due soon' : 'Due in 2-3 weeks'
  return <Badge tone={days < 7 ? 'danger' : 'warning'}>{label}</Badge>
}

interface JournalSectionProps {
  projectId: number
  getProject: (id: number, options: { includeJournal?: boolean }) => Promise<{ project: Project | null; journal_entries?: JournalEntry[] }>
  addJournalEntry: (targetId: number, content: string, targetType: 'project') => Promise<{ status: string; entry: JournalEntry }>
}

// Entries load on first expand: fetching them per row would cost one request per project on every load
const JournalSection = ({ projectId, getProject, addJournalEntry }: JournalSectionProps) => {
  const [entries, setEntries] = useState<JournalEntry[] | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [addError, setAddError] = useState<string | null>(null)
  const [newEntry, setNewEntry] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const result = await getProject(projectId, { includeJournal: true })
      setEntries(result.journal_entries || [])
    } catch (e) {
      setLoadError(`Couldn't load the journal: ${e instanceof Error ? e.message : String(e)}`)
    }
  }, [projectId, getProject])

  const toggle = () => {
    if (!expanded && entries === null) load()
    setExpanded(!expanded)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newEntry.trim() || submitting) return

    setSubmitting(true)
    setAddError(null)
    try {
      const result = await addJournalEntry(projectId, newEntry.trim(), 'project')
      setEntries(prev => [...(prev ?? []), result.entry])
      setNewEntry('')
    } catch (e) {
      setAddError(`Couldn't add the entry: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const formatDate = (dateStr: string) => formatUserDateTime(dateStr, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })

  const loading = expanded && entries === null && !loadError
  return (
    <SubList
      title={<ExpandToggle expanded={expanded} onClick={toggle}>
        Journal{entries && <span className="font-normal text-slate-500"> ({entries.length})</span>}
      </ExpandToggle>}
      empty={expanded && !loadError && (loading ? 'Loading entries...' : 'No journal entries yet')}
      scroll
      footer={expanded && (loadError ? <RowError>{loadError}</RowError> : entries && (
        <form onSubmit={handleSubmit} className="space-y-1 pt-1">
          <div className="flex gap-2">
            <Input type="text" value={newEntry} onChange={e => setNewEntry(e.target.value)} placeholder="Add a journal entry..."
              aria-label="New journal entry" className="flex-1" disabled={submitting} />
            <Button variant="primary" size="sm" type="submit" disabled={!newEntry.trim() || submitting}>{submitting ? '...' : 'Add'}</Button>
          </div>
          {addError && <RowError>{addError}</RowError>}
        </form>
      ))}
    >
      {expanded && entries?.map(entry => (
        <Row
          key={entry.id}
          title={<span className="font-normal whitespace-pre-wrap">{entry.content}</span>}
          badges={entry.private && <Badge tone="warning">private</Badge>}
          meta={[formatDate(entry.created_at)]}
        />
      ))}
    </SubList>
  )
}

interface ProjectRowProps {
  project: Project
  onEdit: () => void
  onDelete: () => void
  onManageTeams: () => void
  inheritedOwner?: { id: number; display_name: string | null; identifier: string } | null
  compact?: boolean
  getProject?: (id: number, options: { includeJournal?: boolean }) => Promise<{ project: Project | null; journal_entries?: JournalEntry[] }>
  addJournalEntry?: (targetId: number, content: string, targetType: 'project') => Promise<{ status: string; entry: JournalEntry }>
  /** The tree view's collapse toggle. */
  leading?: React.ReactNode
  /** The tree view's child projects. */
  children?: React.ReactNode
}

const ProjectRow = ({ project, onEdit, onDelete, onManageTeams, inheritedOwner, compact, getProject, addJournalEntry, leading, children }: ProjectRowProps) => {
  const isGithubBacked = project.repo_path !== null
  const isOpen = project.state === 'open'
  const owner = project.owner || inheritedOwner
  const teamLimit = compact ? 2 : 3
  return (
    <Row
      leading={leading}
      title={project.title}
      muted={!isOpen}
      badges={<>
        <Badge tone={isOpen ? 'success' : 'neutral'}>{project.state}</Badge>
        {isGithubBacked && <Badge tone="primary">GitHub</Badge>}
        {/* Due warnings only matter for open projects */}
        {isOpen && project.due_on && <DueBadge dueOn={project.due_on} />}
      </>}
      meta={[
        owner && `Owner: ${owner.display_name || owner.identifier}${project.owner ? '' : ' (inherited)'}`,
        project.due_on && `Due: ${formatDueDate(project.due_on)}`,
        project.children_count > 0 && `${project.children_count} child${project.children_count !== 1 ? 'ren' : ''}`,
        isGithubBacked && (
          <a href={`https://github.com/${project.repo_path}`} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
            {project.repo_path}
          </a>
        ),
        isGithubBacked && project.number && (
          <a href={`https://github.com/${project.repo_path}/milestone/${project.number}`} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
            Milestone #{project.number}
          </a>
        ),
        // Only http(s) doc links are rendered
        project.doc_url && /^https?:\/\//.test(project.doc_url) && (
          <a href={project.doc_url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline" title={project.doc_url}>Doc</a>
        ),
      ]}
      actions={<>
        <Button size="sm" onClick={onManageTeams}>Teams</Button>
        <Button size="sm" onClick={onEdit}>Edit</Button>
        <ConfirmButton onConfirm={onDelete} disabled={isGithubBacked}
          title={isGithubBacked ? 'GitHub-backed projects cannot be deleted; close them in GitHub instead' : undefined}
          confirm={`Delete "${project.title}"?${project.children_count > 0 ? ` Its ${project.children_count} child project(s) will become top-level.` : ''}`} />
      </>}
    >
      {project.description && !compact && <p className="text-slate-600 line-clamp-2">{project.description}</p>}
      {!!project.teams?.length && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-slate-500">Teams:</span>
          {project.teams.slice(0, teamLimit).map(team => <Badge key={team.id} tone="success">{team.name}</Badge>)}
          {project.teams.length > teamLimit && (
            <Button variant="link" size="sm" onClick={onManageTeams} title={project.teams.slice(teamLimit).map(t => t.name).join(', ')}>
              +{project.teams.length - teamLimit} more
            </Button>
          )}
        </div>
      )}
      {getProject && addJournalEntry && (
        <JournalSection projectId={project.id} getProject={getProject} addJournalEntry={addJournalEntry} />
      )}
      {children}
    </Row>
  )
}

interface ProjectFormModalProps {
  title: string
  project?: Project
  projects: Project[]
  onSubmit: (data: ProjectCreate | ProjectUpdate) => Promise<void>
  onClose: () => void
  availableTeams?: Team[]  // Required for create, used for team selection
  listPeople: ListPeople
}

const ProjectFormModal = ({
  title,
  project,
  projects,
  onSubmit,
  onClose,
  availableTeams = [],
  listPeople,
}: ProjectFormModalProps) => {
  const isEditing = !!project
  const isGithubBacked = project?.repo_path !== null

  const [formData, setFormData] = useState({
    title: project?.title || '',
    description: project?.description || '',
    state: project?.state || 'open' as 'open' | 'closed',
    parent_id: project?.parent_id || null as number | null,
    team_ids: [] as number[],  // For new projects
    owner_id: project?.owner_id || null as number | null,
    due_on: project?.due_on ? project.due_on.slice(0, 10) : '',  // Convert to date input format
    doc_url: project?.doc_url || '',
  })

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [teamsError, setTeamsError] = useState<string | null>(null)

  // Get valid parent options (exclude self and descendants)
  const getValidParents = () => {
    if (!isEditing) {
      return projects
    }

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

    if (!isEditing && formData.team_ids.length === 0) {
      setTeamsError('Select at least one team')
      return
    }

    setSubmitting(true)
    setError(null)
    setTeamsError(null)

    try {
      const dueOnISO = formData.due_on ? `${formData.due_on}T00:00:00Z` : null

      const data: ProjectCreate | ProjectUpdate = isEditing
        ? {
            // For GitHub-backed, only allow parent_id, owner_id, due_on, doc_url changes
            ...(isGithubBacked
              ? {
                  parent_id: formData.parent_id,
                  owner_id: formData.owner_id,
                  due_on: dueOnISO,
                  clear_due_on: !formData.due_on && !!project?.due_on,
                  doc_url: formData.doc_url || null,
                }
              : {
                  title: formData.title || undefined,
                  description: formData.description || null,
                  state: formData.state,
                  parent_id: formData.parent_id,
                  owner_id: formData.owner_id,
                  due_on: dueOnISO,
                  clear_due_on: !formData.due_on && !!project?.due_on,
                  doc_url: formData.doc_url || null,
                }),
          }
        : {
            title: formData.title,
            team_ids: formData.team_ids,
            description: formData.description || null,
            state: formData.state,
            parent_id: formData.parent_id,
            owner_id: formData.owner_id,
            due_on: dueOnISO,
            doc_url: formData.doc_url || null,
          }

      await onSubmit(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save project')
      setSubmitting(false)
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        {isEditing && isGithubBacked && (
          <Notice tone="warning" className="mb-4">
            This project is synced from GitHub. Only parent, owner, due date, and doc URL can be changed here.
            Edit title/description/state in GitHub.
          </Notice>
        )}

        <Field label="Title">
          <Input
            type="text"
            value={formData.title}
            onChange={e => setFormData({ ...formData, title: e.target.value })}
            required={!isEditing}
            disabled={isEditing && isGithubBacked}
            placeholder="Project name"
          />
        </Field>

        <Field label="Description">
          <Textarea
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
            disabled={isEditing && isGithubBacked}
            placeholder="Optional description"
            rows={3}
          />
        </Field>

        <Field label="State">
          <Select
            value={formData.state}
            onChange={e => setFormData({ ...formData, state: e.target.value as 'open' | 'closed' })}
            disabled={isEditing && isGithubBacked}
          >
            <option value="open">Open</option>
            <option value="closed">Closed</option>
          </Select>
        </Field>

        <Field label="Parent project" hint="Organize projects into a hierarchy by setting a parent.">
          <Select
            value={formData.parent_id ?? ''}
            onChange={e => setFormData({
              ...formData,
              parent_id: e.target.value ? Number(e.target.value) : null,
            })}
          >
            <option value="">None (top-level)</option>
            {validParents.map(p => (
              <option key={p.id} value={p.id}>
                {p.title}
                {p.repo_path && ` (${p.repo_path})`}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Owner" hint="Assign someone responsible for this project.">
          <PersonSelect value={formData.owner_id} current={project?.owner} listPeople={listPeople}
            onChange={owner_id => setFormData({ ...formData, owner_id })} />
        </Field>

        <Field label="Due date" hint="Optional deadline for this project.">
          <Input
            type="date"
            value={formData.due_on}
            onChange={e => setFormData({ ...formData, due_on: e.target.value })}
          />
        </Field>

        <Field label="Doc URL" hint="Link to project documentation (Google Doc, Notion, wiki, etc.)">
          <Input
            type="url"
            value={formData.doc_url}
            onChange={e => setFormData({ ...formData, doc_url: e.target.value })}
            placeholder="https://docs.google.com/..."
          />
        </Field>

        {!isEditing && (availableTeams.length === 0 ? (
          <Notice tone="warning">
            No teams available. Create a team first before creating a project.
          </Notice>
        ) : (
          <Field label="Teams" hint="Required: the teams whose members can access this project." error={teamsError}>
            <MultiSelect
              options={availableTeams.map(team => ({ value: String(team.id), label: team.name, description: `@${team.slug}` }))}
              value={formData.team_ids.map(String)}
              onChange={ids => setFormData({ ...formData, team_ids: ids.map(Number) })}
              placeholder="Search teams"
            />
          </Field>
        ))}

        <FormActions>
          <Button type="button" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button variant="primary"
            type="submit"
            disabled={submitting || (!isEditing && availableTeams.length === 0)}
          >
            {submitting ? 'Saving...' : isEditing ? 'Save changes' : 'Create project'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

interface ProjectTeamsModalProps {
  project: Project
  onClose: (hasChanges: boolean) => void
  listTeams: () => Promise<Team[]>
  listProjectTeams: (project: number) => Promise<Team[]>
  assignTeam: (project: number, teamId: number) => Promise<void>
  unassignTeam: (project: number, teamId: number) => Promise<void>
}

const ProjectTeamsModal = ({
  project,
  onClose,
  listTeams,
  listProjectTeams,
  assignTeam,
  unassignTeam,
}: ProjectTeamsModalProps) => {
  const [assignedTeams, setAssignedTeams] = useState<Team[] | null>(null)
  const [allTeams, setAllTeams] = useState<Team[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [hasChanges, setHasChanges] = useState(false)
  const { run, notice } = useActionNotice()

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const [assigned, all] = await Promise.all([listProjectTeams(project.id), listTeams()])
      setAssignedTeams(assigned)
      setAllTeams(all)
    } catch (e) {
      setLoadError(`Couldn't load the teams: ${e instanceof Error ? e.message : String(e)}`)
    }
  }, [project.id, listProjectTeams, listTeams])

  useEffect(() => {
    load()
  }, [load])

  const handleAssign = (team: Team) => run(async () => {
    await assignTeam(project.id, team.id)
    setAssignedTeams(prev => [...(prev ?? []), team])
    setHasChanges(true)
  })

  const handleUnassign = (team: Team) => run(async () => {
    await unassignTeam(project.id, team.id)
    setAssignedTeams(prev => (prev ?? []).filter(t => t.id !== team.id))
    setHasChanges(true)
  })

  const assignedIds = new Set(assignedTeams?.map(t => t.id))
  const availableTeams = allTeams.filter(t => !assignedIds.has(t.id))
  const teamMeta = (team: Team) => [
    `@${team.slug}`,
    team.member_count !== undefined && `${team.member_count} member${team.member_count !== 1 ? 's' : ''}`,
  ]

  const handleClose = () => onClose(hasChanges)

  return (
    <Modal title={`Teams for ${project.title}`} onClose={handleClose}>
      <div className="space-y-4">
        {notice}

        {loadError ? <Notice tone="error" onRetry={load}>{loadError}</Notice>
          : assignedTeams === null ? <LoadingState message="Loading teams..." /> : (
          <>
            <SubList title={`Assigned teams (${assignedTeams.length})`} empty="No teams assigned">
              {assignedTeams.map(team => (
                <Row key={team.id} title={team.name} meta={teamMeta(team)}
                  actions={<ConfirmButton aria-label={`Remove ${team.name}`} onConfirm={() => handleUnassign(team)}
                    confirm={`Remove ${team.name} from ${project.title}? Its members lose access through this team.`}>Remove</ConfirmButton>} />
              ))}
            </SubList>

            {availableTeams.length > 0 && (
              <SubList title="Available teams" scroll>
                {availableTeams.map(team => (
                  <Row key={team.id} title={team.name} meta={teamMeta(team)}
                    actions={<Button size="sm" aria-label={`Add ${team.name}`} onClick={() => handleAssign(team)}>Add</Button>} />
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

export default ProjectsPanel
