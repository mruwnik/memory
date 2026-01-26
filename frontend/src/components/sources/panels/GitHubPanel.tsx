import { useState, useEffect, useCallback } from 'react'
import { useSources, GithubAccount, GithubProject, AvailableRepo, AvailableProject } from '@/hooks/useSources'
import {
  Modal,
  TagsInput,
  IntervalInput,
  EmptyState,
  LoadingState,
  ErrorState,
  StatusBadge,
  SyncStatus,
  SyncButton,
} from '../shared'
import { styles, cx } from '../styles'
import { useSourcesContext } from '../Sources'

export const GitHubPanel = () => {
  const {
    listGithubAccounts,
    addGithubRepo, updateGithubRepo, deleteGithubRepo, syncGithubRepo,
    listAccountProjects, addGithubProject, deleteGithubProject
  } = useSources()
  const { userId } = useSourcesContext()
  const [accounts, setAccounts] = useState<GithubAccount[]>([])
  const [accountProjects, setAccountProjects] = useState<Record<number, GithubProject[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addingRepoTo, setAddingRepoTo] = useState<number | null>(null)
  const [addingProjectTo, setAddingProjectTo] = useState<number | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const accountsData = await listGithubAccounts(userId)
      setAccounts(accountsData)

      // Load projects for each account
      const projectsMap: Record<number, GithubProject[]> = {}
      await Promise.all(
        accountsData.map(async (account) => {
          try {
            projectsMap[account.id] = await listAccountProjects(account.id)
          } catch {
            projectsMap[account.id] = []
          }
        })
      )
      setAccountProjects(projectsMap)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [listGithubAccounts, listAccountProjects, userId])

  useEffect(() => { loadData() }, [loadData])

  const handleAddRepo = async (accountId: number, data: any) => {
    await addGithubRepo(accountId, data)
    setAddingRepoTo(null)
    loadData()
  }

  const handleDeleteRepo = async (accountId: number, repoId: number) => {
    await deleteGithubRepo(accountId, repoId)
    loadData()
  }

  const handleToggleRepoActive = async (accountId: number, repoId: number, active: boolean) => {
    await updateGithubRepo(accountId, repoId, { active: !active })
    loadData()
  }

  const handleSyncRepo = async (accountId: number, repoId: number) => {
    await syncGithubRepo(accountId, repoId)
    loadData()
  }

  const handleAddProject = async (accountId: number, data: { owner: string; project_number: number; is_org: boolean }) => {
    await addGithubProject(accountId, data)
    setAddingProjectTo(null)
    loadData()
  }

  const handleDeleteProject = async (projectId: number) => {
    await deleteGithubProject(projectId)
    loadData()
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  // Require accounts to be set up first
  if (accounts.length === 0) {
    return (
      <div className={styles.panel}>
        <div className={styles.panelHeader}>
          <h3 className={styles.panelTitle}>GitHub</h3>
        </div>
        <EmptyState
          message="No GitHub accounts configured. Add a GitHub account in the Accounts tab first."
        />
      </div>
    )
  }

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>GitHub</h3>
      </div>

      <div className={styles.sourceList}>
        {accounts.map(account => {
          const projects = accountProjects[account.id] || []
          return (
            <div key={account.id} className="border border-slate-200 rounded-lg p-4">
              <div className={styles.cardHeader}>
                <div className={styles.cardInfo}>
                  <h4 className={styles.cardTitle}>{account.name}</h4>
                  <p className={styles.cardSubtitle}>
                    {account.auth_type === 'pat' ? 'Personal Access Token' : 'GitHub App'}
                    {!account.active && ' (disabled)'}
                  </p>
                </div>
                <div className={styles.cardActions}>
                  <StatusBadge active={account.active} />
                </div>
              </div>

              {/* Repositories Section */}
              <div className="mt-4 pt-4 border-t border-slate-100">
                <div className="flex items-center justify-between mb-3">
                  <h5 className="text-sm font-medium text-slate-700">Repositories ({account.repos.length})</h5>
                  <button
                    className={cx(styles.btnAdd, 'text-xs py-1 px-2')}
                    onClick={() => setAddingRepoTo(account.id)}
                    disabled={!account.active}
                  >
                    Add Repo
                  </button>
                </div>

                {account.repos.length === 0 ? (
                  <p className="text-sm text-slate-400 italic">No repositories tracked</p>
                ) : (
                  <div className="space-y-2">
                    {account.repos.map(repo => (
                      <div key={repo.id} className={cx(
                        'flex flex-wrap items-center gap-3 p-3 rounded border',
                        repo.active ? 'border-slate-200 bg-white' : 'border-slate-100 bg-slate-50 opacity-60'
                      )}>
                        <div className="flex-1 min-w-0">
                          <span className="font-medium text-slate-800">{repo.repo_path}</span>
                          <SyncStatus lastSyncAt={repo.last_sync_at} />
                        </div>
                        <div className="flex flex-wrap gap-1">
                          {repo.track_issues && <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs rounded">Issues</span>}
                          {repo.track_prs && <span className="px-2 py-0.5 bg-purple-100 text-purple-700 text-xs rounded">PRs</span>}
                          {repo.track_comments && <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded">Comments</span>}
                        </div>
                        <div className="flex items-center gap-2">
                          <SyncButton
                            onSync={() => handleSyncRepo(account.id, repo.id)}
                            disabled={!repo.active || !account.active}
                            label="Sync"
                          />
                          <button
                            className={styles.btnEdit}
                            onClick={() => handleToggleRepoActive(account.id, repo.id, repo.active)}
                          >
                            {repo.active ? 'Disable' : 'Enable'}
                          </button>
                          <button
                            className={styles.btnDelete}
                            onClick={() => handleDeleteRepo(account.id, repo.id)}
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Projects Section */}
              <div className="mt-4 pt-4 border-t border-slate-100">
                <div className="flex items-center justify-between mb-3">
                  <h5 className="text-sm font-medium text-slate-700">Projects ({projects.length})</h5>
                  <button
                    className={cx(styles.btnAdd, 'text-xs py-1 px-2')}
                    onClick={() => setAddingProjectTo(account.id)}
                    disabled={!account.active}
                  >
                    Add Project
                  </button>
                </div>

                {projects.length === 0 ? (
                  <p className="text-sm text-slate-400 italic">No projects tracked</p>
                ) : (
                  <div className="space-y-2">
                    {projects.map(project => (
                      <div key={project.id} className="flex flex-wrap items-center gap-3 p-3 rounded border border-slate-200 bg-white">
                        <div className="flex-1 min-w-0">
                          <a href={project.url} target="_blank" rel="noopener noreferrer" className="font-medium text-primary hover:underline">
                            {project.owner_login}/#{project.number} - {project.title}
                          </a>
                          <SyncStatus lastSyncAt={project.last_sync_at} />
                        </div>
                        <div className="flex flex-wrap gap-1">
                          <span className="px-2 py-0.5 bg-slate-100 text-slate-700 text-xs rounded">{project.items_total_count} items</span>
                          {project.fields.slice(0, 3).map(field => (
                            <span key={field.id} className="px-2 py-0.5 bg-slate-100 text-slate-600 text-xs rounded">{field.name}</span>
                          ))}
                          {project.fields.length > 3 && (
                            <span className="px-2 py-0.5 bg-slate-100 text-slate-600 text-xs rounded">+{project.fields.length - 3}</span>
                          )}
                        </div>
                        <button
                          className={styles.btnDelete}
                          onClick={() => handleDeleteProject(project.id)}
                        >
                          Remove
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {addingRepoTo && (() => {
        const account = accounts.find(a => a.id === addingRepoTo)
        const existingRepos = account?.repos.map(r => r.repo_path) || []
        const repoIdMap = new Map(account?.repos.map(r => [r.repo_path, r.id]) || [])
        return (
          <GitHubRepoForm
            accountId={addingRepoTo}
            existingRepos={existingRepos}
            repoIdMap={repoIdMap}
            onAdd={(data) => handleAddRepo(addingRepoTo, data)}
            onRemove={(repoId) => handleDeleteRepo(addingRepoTo, repoId)}
            onCancel={() => setAddingRepoTo(null)}
          />
        )
      })()}

      {addingProjectTo && (
        <GitHubProjectForm
          accountId={addingProjectTo}
          existingProjects={accountProjects[addingProjectTo] || []}
          onAdd={(data) => handleAddProject(addingProjectTo, data)}
          onCancel={() => setAddingProjectTo(null)}
        />
      )}
    </div>
  )
}

interface GitHubProjectFormProps {
  accountId: number
  existingProjects: GithubProject[]
  onAdd: (data: { owner: string; project_number: number; is_org: boolean }) => Promise<void>
  onCancel: () => void
}

const GitHubProjectForm = ({ accountId, existingProjects, onAdd, onCancel }: GitHubProjectFormProps) => {
  const [owner, setOwner] = useState('')
  const [isOrg, setIsOrg] = useState(true)
  const [selectedProject, setSelectedProject] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Track which projects are already added
  const existingProjectKeys = new Set(
    existingProjects.map(p => `${p.owner_login}:${p.number}`)
  )

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!owner.trim() || selectedProject === null) {
      setError('Please enter an owner and select a project')
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      await onAdd({
        owner: owner.trim(),
        project_number: selectedProject,
        is_org: isOrg,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add project')
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Add GitHub Project" onClose={onCancel}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Owner (org or username)</label>
          <input
            type="text"
            value={owner}
            onChange={e => {
              setOwner(e.target.value)
              setSelectedProject(null) // Reset selection when owner changes
            }}
            placeholder="e.g., my-organization"
            required
            className={styles.formInput}
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Owner Type</label>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="radio"
                name="ownerType"
                checked={isOrg}
                onChange={() => {
                  setIsOrg(true)
                  setSelectedProject(null)
                }}
                className="rounded-full border-slate-300"
              />
              Organization
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="radio"
                name="ownerType"
                checked={!isOrg}
                onChange={() => {
                  setIsOrg(false)
                  setSelectedProject(null)
                }}
                className="rounded-full border-slate-300"
              />
              User
            </label>
          </div>
        </div>

        {owner.trim() && (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Select Project</label>
            <ProjectSelector
              accountId={accountId}
              owner={owner.trim()}
              isOrg={isOrg}
              existingProjectKeys={existingProjectKeys}
              selectedProject={selectedProject}
              onSelect={setSelectedProject}
            />
          </div>
        )}

        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onCancel} disabled={submitting}>Cancel</button>
          <button
            type="submit"
            className={styles.btnSubmit}
            disabled={submitting || selectedProject === null}
          >
            {submitting ? 'Adding...' : 'Add Project'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

interface ProjectSelectorProps {
  accountId: number
  owner: string
  isOrg: boolean
  existingProjectKeys: Set<string>
  selectedProject: number | null
  onSelect: (projectNumber: number | null) => void
}

const ProjectSelector = ({
  accountId,
  owner,
  isOrg,
  existingProjectKeys,
  selectedProject,
  onSelect,
}: ProjectSelectorProps) => {
  const { listAvailableProjects } = useSources()
  const [projects, setProjects] = useState<AvailableProject[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    const loadProjects = async () => {
      setLoading(true)
      setError(null)
      onSelect(null) // Reset selection when loading new projects
      try {
        const available = await listAvailableProjects(accountId, owner, isOrg, false)
        // Sort by title
        available.sort((a, b) => a.title.localeCompare(b.title))
        setProjects(available)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load projects')
      } finally {
        setLoading(false)
      }
    }
    loadProjects()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onSelect is stable in behavior
    // but defined inline by parent; including it would cause infinite re-fetches
  }, [accountId, owner, isOrg, listAvailableProjects])

  const filteredProjects = projects.filter(project =>
    project.title.toLowerCase().includes(search.toLowerCase()) ||
    (project.short_description?.toLowerCase().includes(search.toLowerCase()) ?? false)
  )

  if (loading) {
    return <div className="text-sm text-slate-500 py-4">Loading projects...</div>
  }

  if (error) {
    return <div className="text-sm text-red-600 py-4">{error}</div>
  }

  if (projects.length === 0) {
    return <div className="text-sm text-slate-500 py-4">No projects found for {owner}</div>
  }

  return (
    <div className="space-y-2">
      <input
        type="text"
        placeholder="Filter projects..."
        value={search}
        onChange={e => setSearch(e.target.value)}
        className={styles.formInput}
      />
      <div className="max-h-60 overflow-y-auto border border-slate-200 rounded-lg">
        {filteredProjects.length === 0 ? (
          <div className="text-sm text-slate-500 p-4 text-center">
            {search ? 'No matching projects' : 'No projects available'}
          </div>
        ) : (
          filteredProjects.map(project => {
            const projectKey = `${owner}:${project.number}`
            const alreadyAdded = existingProjectKeys.has(projectKey)
            return (
              <label
                key={project.number}
                className={cx(
                  'flex items-start gap-3 p-3 border-b border-slate-100 last:border-b-0 cursor-pointer hover:bg-slate-50',
                  alreadyAdded && 'opacity-50 cursor-not-allowed'
                )}
              >
                <input
                  type="radio"
                  name="project-select"
                  checked={selectedProject === project.number}
                  onChange={() => onSelect(project.number)}
                  disabled={alreadyAdded}
                  className="mt-0.5 rounded-full border-slate-300"
                />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-slate-800">
                    #{project.number} - {project.title}
                    {alreadyAdded && <span className="ml-2 text-xs text-slate-500">(already added)</span>}
                    {project.closed && <span className="ml-2 text-xs text-slate-500">(closed)</span>}
                  </div>
                  <div className="text-sm text-slate-500">
                    {project.items_total_count} items
                    {project.short_description && ` • ${project.short_description}`}
                  </div>
                </div>
              </label>
            )
          })
        )}
      </div>
    </div>
  )
}

// RepoSelector - scrollable checkbox list showing all repos with monitoring status
interface RepoSelectorProps {
  accountId: number
  monitoredRepos: Set<string> // repos currently being monitored (full_name format)
  onChange: (monitored: Set<string>) => void
}

const RepoSelector = ({ accountId, monitoredRepos, onChange }: RepoSelectorProps) => {
  const { listAvailableRepos } = useSources()
  const [repos, setRepos] = useState<AvailableRepo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    const loadRepos = async () => {
      setLoading(true)
      setError(null)
      try {
        const available = await listAvailableRepos(accountId)
        // Sort by full_name
        available.sort((a, b) => a.full_name.localeCompare(b.full_name))
        setRepos(available)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load repositories')
      } finally {
        setLoading(false)
      }
    }
    loadRepos()
  }, [accountId, listAvailableRepos])

  const filteredRepos = repos.filter(repo =>
    repo.full_name.toLowerCase().includes(search.toLowerCase()) ||
    (repo.description?.toLowerCase().includes(search.toLowerCase()) ?? false)
  )

  const toggleRepo = (fullName: string) => {
    const newMonitored = new Set(monitoredRepos)
    if (newMonitored.has(fullName)) {
      newMonitored.delete(fullName)
    } else {
      newMonitored.add(fullName)
    }
    onChange(newMonitored)
  }

  const selectAll = () => {
    const newMonitored = new Set(monitoredRepos)
    filteredRepos.forEach(repo => newMonitored.add(repo.full_name))
    onChange(newMonitored)
  }

  const selectNone = () => {
    const newMonitored = new Set(monitoredRepos)
    filteredRepos.forEach(repo => newMonitored.delete(repo.full_name))
    onChange(newMonitored)
  }

  if (loading) {
    return (
      <div className="min-h-60 flex items-center justify-center text-sm text-slate-500">
        Loading repositories...
      </div>
    )
  }

  if (error) {
    return <div className="text-sm text-red-600 py-4">{error}</div>
  }

  return (
    <div className="space-y-2">
      <input
        type="text"
        placeholder="Filter repositories..."
        value={search}
        onChange={e => setSearch(e.target.value)}
        className={styles.formInput}
      />
      <div className="flex items-center gap-2 text-sm">
        <button type="button" onClick={selectAll} className="text-primary hover:underline">All</button>
        <button type="button" onClick={selectNone} className="text-primary hover:underline">None</button>
        <span className="text-slate-500 ml-auto">
          {monitoredRepos.size} monitored of {repos.length}
        </span>
      </div>
      <div className="max-h-60 overflow-y-auto border border-slate-200 rounded-lg">
        {filteredRepos.length === 0 ? (
          <div className="text-sm text-slate-500 p-4 text-center">
            {search ? 'No matching repositories' : 'No repositories available'}
          </div>
        ) : (
          filteredRepos.map(repo => (
            <label key={repo.full_name} className="flex items-start gap-3 p-3 border-b border-slate-100 last:border-b-0 cursor-pointer hover:bg-slate-50">
              <input
                type="checkbox"
                checked={monitoredRepos.has(repo.full_name)}
                onChange={() => toggleRepo(repo.full_name)}
                className="mt-0.5 rounded border-slate-300"
              />
              <div className="flex-1 min-w-0">
                <div className="font-medium text-slate-800">
                  {repo.full_name}
                  {repo.private && <span className="ml-2 px-1.5 py-0.5 bg-slate-100 text-slate-600 text-xs rounded">private</span>}
                </div>
                {repo.description && (
                  <div className="text-sm text-slate-500 truncate">{repo.description}</div>
                )}
              </div>
            </label>
          ))
        )}
      </div>
    </div>
  )
}

interface GitHubRepoFormProps {
  accountId: number
  existingRepos: string[] // repo_path format like "owner/name"
  repoIdMap: Map<string, number> // map repo_path to repo id for deletion
  onAdd: (data: any) => Promise<void>
  onRemove: (repoId: number) => Promise<void>
  onCancel: () => void
}

const GitHubRepoForm = ({ accountId, existingRepos, repoIdMap, onAdd, onRemove, onCancel }: GitHubRepoFormProps) => {
  const [monitoredRepos, setMonitoredRepos] = useState<Set<string>>(() => new Set(existingRepos))
  const [formData, setFormData] = useState({
    track_issues: true,
    track_prs: true,
    track_comments: true,
    track_project_fields: false,
    tags: [] as string[],
    check_interval: 60,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<string | null>(null)

  // Calculate what changed
  const initialSet = new Set(existingRepos)
  const toAdd = Array.from(monitoredRepos).filter(r => !initialSet.has(r))
  const toRemove = existingRepos.filter(r => !monitoredRepos.has(r))
  const hasChanges = toAdd.length > 0 || toRemove.length > 0

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!hasChanges) {
      onCancel()
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      // Remove unselected repos
      for (const repoPath of toRemove) {
        const repoId = repoIdMap.get(repoPath)
        if (repoId) {
          setProgress(`Removing ${repoPath}...`)
          await onRemove(repoId)
        }
      }

      // Add newly selected repos
      for (const repoPath of toAdd) {
        const [owner, name] = repoPath.split('/')
        setProgress(`Adding ${repoPath}...`)
        await onAdd({ ...formData, owner, name })
      }

      onCancel() // Close modal on success
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update repositories')
    } finally {
      setSubmitting(false)
      setProgress(null)
    }
  }

  return (
    <Modal title="Manage Repositories" onClose={onCancel}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <RepoSelector
            accountId={accountId}
            monitoredRepos={monitoredRepos}
            onChange={setMonitoredRepos}
          />
        </div>

        {toAdd.length > 0 && (
          <>
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Track (for new repos):</label>
              <div className="flex flex-wrap gap-4 mt-2">
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={formData.track_issues}
                    onChange={e => setFormData({ ...formData, track_issues: e.target.checked })}
                    className="rounded border-slate-300"
                  />
                  Issues
                </label>
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={formData.track_prs}
                    onChange={e => setFormData({ ...formData, track_prs: e.target.checked })}
                    className="rounded border-slate-300"
                  />
                  Pull Requests
                </label>
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={formData.track_comments}
                    onChange={e => setFormData({ ...formData, track_comments: e.target.checked })}
                    className="rounded border-slate-300"
                  />
                  Comments
                </label>
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={formData.track_project_fields}
                    onChange={e => setFormData({ ...formData, track_project_fields: e.target.checked })}
                    className="rounded border-slate-300"
                  />
                  Project Fields
                </label>
              </div>
            </div>

            <IntervalInput
              value={formData.check_interval}
              onChange={check_interval => setFormData({ ...formData, check_interval })}
              label="Check interval (new repos)"
            />

            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Tags (new repos)</label>
              <TagsInput
                tags={formData.tags}
                onChange={tags => setFormData({ ...formData, tags })}
              />
            </div>
          </>
        )}

        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onCancel} disabled={submitting}>Cancel</button>
          <button type="submit" className={styles.btnSubmit} disabled={submitting || !hasChanges}>
            {progress
              ? progress
              : !hasChanges
                ? 'No changes'
                : toAdd.length > 0 && toRemove.length > 0
                  ? `Add ${toAdd.length}, Remove ${toRemove.length}`
                  : toAdd.length > 0
                    ? `Add ${toAdd.length} ${toAdd.length === 1 ? 'Repository' : 'Repositories'}`
                    : `Remove ${toRemove.length} ${toRemove.length === 1 ? 'Repository' : 'Repositories'}`}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default GitHubPanel
