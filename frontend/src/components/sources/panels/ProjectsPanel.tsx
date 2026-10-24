import { useState, useEffect, useCallback, useRef } from 'react'
import { useProjects, Project, ProjectTreeNode, ProjectCreate, ProjectUpdate, JournalEntry } from '@/hooks/useProjects'
import { useTeams, Team, TeamMember } from '@/hooks/useTeams'
import { useJournal } from '@/hooks/useJournal'
import { Person } from '@/hooks/usePeople'
import { Badge, Button, ConfirmButton, cx, EmptyState, ExpandToggle, Field, FormActions, Hint, Input, Label, List, Modal, Notice, Page, Row, Select, SubList, Textarea, ToggleGroup, Toolbar } from '@/components/shared/ui'

// Team members popover component
interface TeamMembersPopoverProps {
  team: { id: number; name: string }
  onClose: () => void
  listMembers: (team: number) => Promise<TeamMember[]>
}

const TeamMembersPopover = ({ team, onClose, listMembers }: TeamMembersPopoverProps) => {
  const [members, setMembers] = useState<TeamMember[]>([])
  const [loading, setLoading] = useState(true)
  const popoverRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const loadMembers = async () => {
      setLoading(true)
      try {
        const data = await listMembers(team.id)
        setMembers(data)
      } catch {
        setMembers([])
      } finally {
        setLoading(false)
      }
    }
    loadMembers()
  }, [team.id, listMembers])

  // Close on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  return (
    <div
      ref={popoverRef}
      className="absolute z-50 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg p-3 min-w-48 max-w-64"
    >
      <div className="font-medium text-slate-800 mb-2 pb-2 border-b border-slate-100">
        {team.name}
      </div>
      {loading ? (
        <p className="text-sm text-slate-500">Loading...</p>
      ) : members.length === 0 ? (
        <p className="text-sm text-slate-500">No members</p>
      ) : (
        <ul className="space-y-1.5">
          {members.map(member => (
            <li key={member.id} className="flex items-center gap-2 text-sm">
              <span className="text-slate-800">{member.display_name || member.identifier}</span>
              {member.role && (
                <span className={cx(
                  'px-1.5 py-0.5 rounded text-xs',
                  member.role === 'admin' ? 'bg-purple-100 text-purple-700' :
                  member.role === 'lead' ? 'bg-blue-100 text-blue-700' :
                  'bg-slate-100 text-slate-600'
                )}>
                  {member.role}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Due date selector popover component
interface DueDateSelectorPopoverProps {
  currentDueOn: string | null
  onSelect: (dueOn: string | null) => void
  onClose: () => void
}

const DueDateSelectorPopover = ({ currentDueOn, onSelect, onClose }: DueDateSelectorPopoverProps) => {
  const popoverRef = useRef<HTMLDivElement>(null)
  const [dateValue, setDateValue] = useState(currentDueOn ? currentDueOn.split('T')[0] : '')

  // Close on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  const handleApply = () => {
    if (dateValue) {
      onSelect(new Date(dateValue + 'T00:00:00Z').toISOString())
    } else {
      onSelect(null)
    }
    onClose()
  }

  const handleClear = () => {
    onSelect(null)
    onClose()
  }

  return (
    <div
      ref={popoverRef}
      className="absolute z-50 mt-1 left-0 bg-white border border-slate-200 rounded-lg shadow-lg p-3 min-w-56"
    >
      <Input
        type="date"
        value={dateValue}
        onChange={e => setDateValue(e.target.value)}
        className="mb-2"
        autoFocus
      />
      <div className="flex gap-2">
        <Button variant="primary" size="sm"
          type="button"
          onClick={handleApply}
          className="flex-1"
        >
          Apply
        </Button>
        {currentDueOn && (
          <Button variant="danger" size="sm"
            type="button"
            onClick={handleClear}
          >
            Clear
          </Button>
        )}
      </div>
    </div>
  )
}

// Owner selector popover component
interface OwnerSelectorPopoverProps {
  currentOwnerId: number | null
  availablePeople: Person[]
  onSelect: (ownerId: number | null) => void
  onClose: () => void
}

const OwnerSelectorPopover = ({ currentOwnerId, availablePeople, onSelect, onClose }: OwnerSelectorPopoverProps) => {
  const popoverRef = useRef<HTMLDivElement>(null)
  const [search, setSearch] = useState('')

  // Close on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  const filteredPeople = search.trim()
    ? availablePeople.filter(p =>
        (p.display_name?.toLowerCase().includes(search.toLowerCase())) ||
        p.identifier.toLowerCase().includes(search.toLowerCase())
      )
    : availablePeople

  return (
    <div
      ref={popoverRef}
      className="absolute z-50 mt-1 left-0 bg-white border border-slate-200 rounded-lg shadow-lg p-2 min-w-56 max-w-72"
    >
      <Input
        type="text"
        placeholder="Search people..."
        value={search}
        onChange={e => setSearch(e.target.value)}
        className="mb-2"
        autoFocus
      />
      <div className="max-h-48 overflow-y-auto">
        {/* None option */}
        <button
          type="button"
          onClick={() => { onSelect(null); onClose() }}
          className={cx(
            'w-full text-left px-2 py-1.5 rounded text-sm transition-colors',
            currentOwnerId === null
              ? 'bg-primary/10 text-primary'
              : 'hover:bg-slate-50 text-slate-500 italic'
          )}
        >
          None
        </button>
        {filteredPeople.map(person => (
          <button
            key={person.id}
            type="button"
            onClick={() => { onSelect(person.id); onClose() }}
            className={cx(
              'w-full text-left px-2 py-1.5 rounded text-sm transition-colors',
              currentOwnerId === person.id
                ? 'bg-primary/10 text-primary'
                : 'hover:bg-slate-50 text-slate-700'
            )}
          >
            {person.display_name || person.identifier}
          </button>
        ))}
        {filteredPeople.length === 0 && search && (
          <p className="text-sm text-slate-500 px-2 py-1.5">No matches</p>
        )}
      </div>
    </div>
  )
}

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
    listMembers,
    listProjectTeams,
    assignTeamToProject,
    unassignTeamFromProject,
    getTeam,
  } = useTeams()

  const { addJournalEntry } = useJournal()

  const [projects, setProjects] = useState<Project[]>([])
  const [tree, setTree] = useState<ProjectTreeNode[]>([])
  const [availableTeams, setAvailableTeams] = useState<Team[]>([])
  const [availablePeople, setAvailablePeople] = useState<Person[]>([])
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
      const [projectsList, treeData, teamsList, adminTeam, internalTeam] = await Promise.all([
        listProjects({ state, include_teams: true, limit: 500 }),
        getProjectTree({ state }),
        listTeams(),
        getTeam('admin', true),
        getTeam('internal', true),
      ])
      // Ensure we always have arrays (API might return error objects)
      setProjects(Array.isArray(projectsList) ? projectsList : [])
      setTree(Array.isArray(treeData) ? treeData : [])
      setAvailableTeams(Array.isArray(teamsList) ? teamsList : [])

      // Combine and deduplicate members from admin and internal teams
      const memberMap = new Map<number, Person>()
      for (const team of [adminTeam, internalTeam]) {
        if (team?.members) {
          for (const member of team.members) {
            if (!memberMap.has(member.id)) {
              memberMap.set(member.id, {
                id: member.id,
                identifier: member.identifier,
                display_name: member.display_name,
                aliases: [],
                contact_info: {},
                tags: [],
                created_at: null,
              })
            }
          }
        }
      }
      setAvailablePeople(Array.from(memberMap.values()))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load projects')
    } finally {
      setLoading(false)
    }
  }, [listProjects, getProjectTree, listTeams, getTeam, stateFilter])

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

  const handleDelete = async (project: Project) => {
    await deleteProject(project.id)
    loadData()
  }

  const handleChangeOwner = useCallback(async (projectId: number, ownerId: number | null) => {
    const result = await updateProject(projectId, {
      owner_id: ownerId,
    })
    // Update local state instead of reloading everything
    if (result.success) {
      // Find the owner from availablePeople to get display info
      const owner = ownerId ? availablePeople.find(p => p.id === ownerId) : null
      setProjects(prev => prev.map(p => p.id === projectId ? {
        ...p,
        owner_id: ownerId,
        owner: owner ? { id: owner.id, identifier: owner.identifier, display_name: owner.display_name } : null,
      } : p))
    }
  }, [updateProject, availablePeople])

  const handleChangeDueDate = useCallback(async (projectId: number, dueOn: string | null) => {
    const result = await updateProject(projectId, {
      due_on: dueOn,
      clear_due_on: dueOn === null,
    })
    // Update local state instead of reloading everything
    if (result.success) {
      setProjects(prev => prev.map(p => p.id === projectId ? {
        ...p,
        due_on: dueOn,
      } : p))
    }
  }, [updateProject])

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

      {projects.length === 0 ? (
        <EmptyState
          message="No projects found. Create a standalone project or sync GitHub milestones."
          actionLabel="Create Project"
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
              onChangeOwner={handleChangeOwner}
              onChangeDueDate={handleChangeDueDate}
              projects={projects}
              collapsedNodes={collapsedNodes}
              onToggleCollapse={toggleCollapse}
              listMembers={listMembers}
              availablePeople={availablePeople}
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
                  onChangeOwner={handleChangeOwner}
                  onChangeDueDate={handleChangeDueDate}
                  listMembers={listMembers}
                  availablePeople={availablePeople}
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
          title="Create Project"
          projects={projects}
          onSubmit={handleCreate}
          onClose={() => setShowCreateModal(false)}
          availableTeams={availableTeams}
          availablePeople={availablePeople}
        />
      )}

      {editingProject && (
        <ProjectFormModal
          title="Edit Project"
          project={editingProject}
          projects={projects}
          onSubmit={data => handleUpdate(editingProject.id, data)}
          onClose={() => setEditingProject(null)}
          availablePeople={availablePeople}
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
  onChangeOwner: (projectId: number, ownerId: number | null) => Promise<void>
  onChangeDueDate: (projectId: number, dueOn: string | null) => Promise<void>
  projects: Project[]
  collapsedNodes: Set<number>
  onToggleCollapse: (nodeId: number) => void
  listMembers: (team: number) => Promise<TeamMember[]>
  availablePeople: Person[]
  inheritedOwner?: { id: number; display_name: string | null; identifier: string } | null
  depth?: number
  getProject: (id: number, options: { includeJournal?: boolean }) => Promise<{ project: Project | null; journal_entries?: JournalEntry[] }>
  addJournalEntry: (targetId: number, content: string, targetType: 'project') => Promise<{ status: string; entry: JournalEntry }>
}

const ProjectTree = ({ nodes, onEdit, onDelete, onManageTeams, onChangeOwner, onChangeDueDate, projects, collapsedNodes, onToggleCollapse, listMembers, availablePeople, inheritedOwner, depth = 0, getProject, addJournalEntry }: ProjectTreeProps) => {
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
        onChangeOwner={onChangeOwner}
        onChangeDueDate={onChangeDueDate}
        listMembers={listMembers}
        availablePeople={availablePeople}
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
              onChangeOwner={onChangeOwner}
              onChangeDueDate={onChangeDueDate}
              projects={projects}
              collapsedNodes={collapsedNodes}
              onToggleCollapse={onToggleCollapse}
              listMembers={listMembers}
              availablePeople={availablePeople}
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

// Due date warning levels
type DueWarningLevel = 'critical' | 'caution' | 'none' | 'no-date'

const getDueWarningLevel = (dueOn: string | null): DueWarningLevel => {
  if (!dueOn) return 'no-date'

  const now = new Date()
  const dueDate = new Date(dueOn)
  const diffMs = dueDate.getTime() - now.getTime()
  const diffDays = diffMs / (1000 * 60 * 60 * 24)

  if (diffDays < 0) return 'critical'  // Overdue
  if (diffDays < 7) return 'critical'  // Under 1 week
  if (diffDays <= 21) return 'caution' // 2-3 weeks
  return 'none'
}

const formatDueDate = (dueOn: string): string => {
  const date = new Date(dueOn)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

// Journal section component
interface JournalSectionProps {
  projectId: number
  getProject: (id: number, options: { includeJournal?: boolean }) => Promise<{ project: Project | null; journal_entries?: JournalEntry[] }>
  addJournalEntry: (targetId: number, content: string, targetType: 'project') => Promise<{ status: string; entry: JournalEntry }>
}

const JournalSection = ({ projectId, getProject, addJournalEntry }: JournalSectionProps) => {
  const [entries, setEntries] = useState<JournalEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(false)
  const [newEntry, setNewEntry] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Load entries on mount
  useEffect(() => {
    let cancelled = false
    const loadEntries = async () => {
      try {
        const result = await getProject(projectId, { includeJournal: true })
        if (!cancelled) {
          setEntries(result.journal_entries || [])
        }
      } catch (e) {
        console.error('Failed to load journal entries:', e)
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }
    loadEntries()
    return () => { cancelled = true }
  }, [projectId, getProject])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newEntry.trim() || submitting) return

    setSubmitting(true)
    try {
      const result = await addJournalEntry(projectId, newEntry.trim(), 'project')
      setEntries(prev => [...prev, result.entry])
      setNewEntry('')
    } catch (e) {
      console.error('Failed to add journal entry:', e)
    } finally {
      setSubmitting(false)
    }
  }

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  return (
    <SubList
      title={<ExpandToggle expanded={expanded} onClick={() => setExpanded(!expanded)}>
        Journal <span className="font-normal text-slate-500">({loading ? '...' : entries.length})</span>
      </ExpandToggle>}
      empty={expanded && (loading ? 'Loading entries...' : 'No journal entries yet')}
      scroll
      footer={expanded && !loading && (
        <form onSubmit={handleSubmit} className="flex gap-2 pt-1">
          <Input type="text" value={newEntry} onChange={e => setNewEntry(e.target.value)} placeholder="Add a journal entry..."
            aria-label="New journal entry" className="flex-1" disabled={submitting} />
          <Button variant="primary" size="sm" type="submit" disabled={!newEntry.trim() || submitting}>{submitting ? '...' : 'Add'}</Button>
        </form>
      )}
    >
      {expanded && !loading && entries.map(entry => (
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

// Project row component
interface ProjectCardProps {
  project: Project
  onEdit: () => void
  onDelete: () => void
  onManageTeams: () => void
  onChangeOwner?: (projectId: number, ownerId: number | null) => Promise<void>
  onChangeDueDate?: (projectId: number, dueOn: string | null) => Promise<void>
  listMembers?: (team: number) => Promise<TeamMember[]>
  availablePeople?: Person[]
  inheritedOwner?: { id: number; display_name: string | null; identifier: string } | null
  compact?: boolean
  getProject?: (id: number, options: { includeJournal?: boolean }) => Promise<{ project: Project | null; journal_entries?: JournalEntry[] }>
  addJournalEntry?: (targetId: number, content: string, targetType: 'project') => Promise<{ status: string; entry: JournalEntry }>
  /** The tree view's collapse toggle. */
  leading?: React.ReactNode
  /** The tree view's child projects. */
  children?: React.ReactNode
}

const ProjectRow = ({ project, onEdit, onDelete, onManageTeams, onChangeOwner, onChangeDueDate, listMembers, availablePeople, inheritedOwner, compact, getProject, addJournalEntry, leading, children }: ProjectCardProps) => {
  const isGithubBacked = project.repo_path !== null
  const isOpen = project.state === 'open'
  const dueWarning = getDueWarningLevel(project.due_on)
  const hasOwnOwner = project.owner_id !== null
  const effectiveOwner = project.owner || inheritedOwner
  const isInheritedOwner = !hasOwnOwner && !!inheritedOwner
  const [expandedTeam, setExpandedTeam] = useState<{ id: number; name: string } | null>(null)
  const [showOwnerSelector, setShowOwnerSelector] = useState(false)
  const [showDueDateSelector, setShowDueDateSelector] = useState(false)

  const handleTeamClick = (e: React.MouseEvent, team: { id: number; name: string }) => {
    e.stopPropagation()
    if (listMembers) {
      setExpandedTeam(expandedTeam?.id === team.id ? null : team)
    } else {
      onManageTeams()
    }
  }

  const handleOwnerClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (onChangeOwner && availablePeople) {
      setShowOwnerSelector(!showOwnerSelector)
    }
  }

  const handleOwnerSelect = async (ownerId: number | null) => {
    if (onChangeOwner) {
      await onChangeOwner(project.id, ownerId)
    }
  }

  const handleDueDateClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (onChangeDueDate) {
      setShowDueDateSelector(!showDueDateSelector)
    }
  }

  const handleDueDateSelect = async (dueOn: string | null) => {
    if (onChangeDueDate) {
      await onChangeDueDate(project.id, dueOn)
    }
  }

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
        {isOpen && dueWarning === 'critical' && (
          <Badge tone="danger" title={project.due_on ? `Due: ${formatDueDate(project.due_on)}` : undefined}>
            {project.due_on && new Date(project.due_on) < new Date() ? 'Overdue' : 'Due soon'}
          </Badge>
        )}
        {isOpen && dueWarning === 'caution' && (
          <Badge tone="warning" title={project.due_on ? `Due: ${formatDueDate(project.due_on)}` : undefined}>Due in 2-3 weeks</Badge>
        )}
      </>}
      meta={[
        <span className="relative">
          {effectiveOwner && 'Owner: '}
          <button type="button" onClick={handleOwnerClick}
            className={cx('rounded px-1 hover:bg-slate-100 max-sm:py-2',
              effectiveOwner ? (isInheritedOwner ? 'italic text-slate-600' : 'text-slate-700') : 'bg-amber-50 text-amber-800')}
            title={isInheritedOwner ? 'Inherited from parent' : effectiveOwner ? 'Change owner' : undefined}>
            {effectiveOwner
              ? `${effectiveOwner.display_name || effectiveOwner.identifier}${isInheritedOwner ? ' (inherited)' : ''}`
              : 'Set owner'}
          </button>
          {showOwnerSelector && onChangeOwner && availablePeople && (
            <OwnerSelectorPopover currentOwnerId={project.owner_id} availablePeople={availablePeople}
              onSelect={handleOwnerSelect} onClose={() => setShowOwnerSelector(false)} />
          )}
        </span>,
        <span className="relative">
          {project.due_on && 'Due: '}
          <button type="button" onClick={handleDueDateClick} className="rounded px-1 text-slate-700 hover:bg-slate-100 max-sm:py-2"
            title={project.due_on ? 'Change due date' : undefined}>
            {project.due_on ? formatDueDate(project.due_on) : 'Set due date'}
          </button>
          {showDueDateSelector && onChangeDueDate && (
            <DueDateSelectorPopover currentDueOn={project.due_on} onSelect={handleDueDateSelect}
              onClose={() => setShowDueDateSelector(false)} />
          )}
        </span>,
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
          {project.teams.slice(0, teamLimit).map(team => (
            <span key={team.id} className="relative">
              <button type="button" onClick={e => handleTeamClick(e, team)} className="rounded hover:opacity-80">
                <Badge tone="success">{team.name}</Badge>
              </button>
              {expandedTeam?.id === team.id && listMembers && (
                <TeamMembersPopover team={team} onClose={() => setExpandedTeam(null)} listMembers={listMembers} />
              )}
            </span>
          ))}
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

// Form modal for create/edit
interface ProjectFormModalProps {
  title: string
  project?: Project
  projects: Project[]
  onSubmit: (data: ProjectCreate | ProjectUpdate) => Promise<void>
  onClose: () => void
  availableTeams?: Team[]  // Required for create, used for team selection
  availablePeople?: Person[]  // For owner selection
}

const ProjectFormModal = ({
  title,
  project,
  projects,
  onSubmit,
  onClose,
  availableTeams = [],
  availablePeople = [],
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
    due_on: project?.due_on ? project.due_on.split('T')[0] : '',  // Convert to date input format
    doc_url: project?.doc_url || '',
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
      // Convert date to ISO string if provided
      const dueOnISO = formData.due_on ? new Date(formData.due_on + 'T00:00:00Z').toISOString() : null

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

        <Field label="Parent Project" hint="Organize projects into a hierarchy by setting a parent.">
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
          <Select
            value={formData.owner_id ?? ''}
            onChange={e => setFormData({
              ...formData,
              owner_id: e.target.value ? Number(e.target.value) : null,
            })}
          >
            <option value="">No owner</option>
            {availablePeople.map(person => (
              <option key={person.id} value={person.id}>
                {person.display_name || person.identifier}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Due Date" hint="Optional deadline for this project.">
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

        {/* Team selection - only for new projects */}
        {!isEditing && (
          <Field>
            <Label>
              Teams <span className="text-red-500">*</span>
            </Label>
            <p className="text-sm text-slate-500 mb-2">
              Select which teams will have access to this project.
            </p>
            {availableTeams.length === 0 ? (
              <Notice tone="warning">
                No teams available. Create a team first before creating a project.
              </Notice>
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
                      className="text-purple-600"
                    />
                    <span className="font-medium text-slate-900">{team.name}</span>
                    <span className="text-sm text-slate-500">@{team.slug}</span>
                  </label>
                ))}
              </div>
            )}
            {formData.team_ids.length > 0 && (
              <Hint>
                {formData.team_ids.length} team{formData.team_ids.length !== 1 ? 's' : ''} selected
              </Hint>
            )}
          </Field>
        )}

        <FormActions>
          <Button type="button" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button variant="primary"
            type="submit"
            disabled={submitting || (!isEditing && availableTeams.length === 0)}
          >
            {submitting ? 'Saving...' : isEditing ? 'Update' : 'Create'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

// Project Teams Management Modal
interface ProjectTeamsModalProps {
  project: Project
  onClose: (hasChanges: boolean) => void
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
  const [hasChanges, setHasChanges] = useState(false)

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
      setHasChanges(true)
    } else {
      setError(result.error || 'Failed to assign team')
    }
  }

  const handleUnassign = async (team: Team) => {
    setError(null)
    const result = await unassignTeam(project.id, team.id)
    if (result.success) {
      setAssignedTeams(prev => prev.filter(t => t.id !== team.id))
      setHasChanges(true)
    } else {
      setError(result.error || 'Failed to unassign team')
    }
  }

  const assignedIds = new Set(assignedTeams.map(t => t.id))
  const availableTeams = allTeams.filter(t => !assignedIds.has(t.id))

  const handleClose = () => onClose(hasChanges)

  return (
    <Modal title={`Teams for ${project.title}`} onClose={handleClose}>
      <div className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

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
                          <span className="text-slate-500 text-xs ml-2">
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
