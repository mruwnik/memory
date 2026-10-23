import { useState, useEffect, useCallback } from 'react'
import { useSources, GoogleAccount, GoogleFolder, DriveItem } from '@/hooks/useSources'
import { useProjects, Project } from '@/hooks/useProjects'
import { TagsInput, IntervalInput, StatusBadge, SyncStatus, SyncButton } from '../shared'
import { Button, Card, Checkbox, EmptyState, ErrorState, Field, FieldRow, FormActions, Input, LoadingState, Modal, Notice, Select, cx } from '@/components/shared/ui'
import { useSourcesContext } from '../Sources'

export const GoogleDrivePanel = () => {
  const {
    listGoogleAccounts,
    addGoogleFolder, updateGoogleFolder, deleteGoogleFolder, syncGoogleFolder
  } = useSources()
  const { listProjects } = useProjects()
  const { userId } = useSourcesContext()
  const [accounts, setAccounts] = useState<GoogleAccount[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addingFolderTo, setAddingFolderTo] = useState<number | null>(null)
  const [browsingFoldersFor, setBrowsingFoldersFor] = useState<number | null>(null)
  const [managingExclusionsFor, setManagingExclusionsFor] = useState<{ accountId: number; folder: GoogleFolder } | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [accountsData, projectsData] = await Promise.all([
        listGoogleAccounts(userId),
        listProjects()
      ])
      setAccounts(accountsData)
      setProjects(projectsData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [listGoogleAccounts, listProjects, userId])

  useEffect(() => { loadData() }, [loadData])

  const handleAddFolder = async (accountId: number, data: any) => {
    await addGoogleFolder(accountId, data)
    setAddingFolderTo(null)
    loadData()
  }

  const handleBrowseSelect = async (accountId: number, items: SelectedItem[]) => {
    for (const item of items) {
      await addGoogleFolder(accountId, {
        folder_id: item.id,
        folder_name: item.name,
        recursive: item.is_folder ? item.recursive : false,
      })
    }
    setBrowsingFoldersFor(null)
    loadData()
  }

  const handleUpdateExclusions = async (accountId: number, folderId: number, excludeIds: string[]) => {
    await updateGoogleFolder(accountId, folderId, { exclude_folder_ids: excludeIds })
    setManagingExclusionsFor(null)
    loadData()
  }

  const handleDeleteFolder = async (accountId: number, folderId: number) => {
    await deleteGoogleFolder(accountId, folderId)
    loadData()
  }

  const handleToggleFolderActive = async (accountId: number, folderId: number, active: boolean) => {
    await updateGoogleFolder(accountId, folderId, { active: !active })
    loadData()
  }

  const handleSyncFolder = async (accountId: number, folderId: number) => {
    await syncGoogleFolder(accountId, folderId)
    loadData()
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  // Show message if no accounts connected
  if (accounts.length === 0) {
    return (
      <Card title="Google Drive">
        <EmptyState
          message="No Google accounts connected. Add a Google account in the Accounts tab first."
        />
      </Card>
    )
  }

  return (
    <Card title="Google Drive Folders">
      <div className="space-y-3">
        {accounts.map(account => (
          <div key={account.id} className="border border-slate-200 rounded-lg p-4">
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1 min-w-0">
                <h4 className="font-medium text-slate-800 truncate">{account.email}</h4>
              </div>
              <div className="flex flex-wrap items-center gap-2 shrink-0">
                <StatusBadge active={account.active} />
              </div>
            </div>

            {account.sync_error && (
              <Notice tone="error">{account.sync_error}</Notice>
            )}

            <div className="mt-4 pt-4 border-t border-slate-100">
              <div className="flex items-center justify-between mb-3">
                <h5 className="text-sm font-medium text-slate-700">Synced Folders ({account.folders.length})</h5>
                <div className="flex gap-2">
                  <Button variant="primary" className="text-xs py-1 px-2" onClick={() => setBrowsingFoldersFor(account.id)}>Browse & Add</Button>
                  <Button className="text-xs py-1 px-2" onClick={() => setAddingFolderTo(account.id)}>Add by ID</Button>
                </div>
              </div>

              {account.folders.length === 0 ? (
                <p className="text-sm text-slate-400 italic">No folders configured for sync</p>
              ) : (
                <div className="space-y-2">
                  {account.folders.map(folder => (
                    <div key={folder.id} className={cx(
                      'flex flex-wrap items-center gap-3 p-3 rounded border',
                      folder.active ? 'border-slate-200 bg-white' : 'border-slate-100 bg-slate-50 opacity-60'
                    )}>
                      <div className="flex-1 min-w-0">
                        <a
                          className="font-medium text-primary hover:underline"
                          href={`https://drive.google.com/open?id=${folder.folder_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {folder.folder_name}
                        </a>
                        {folder.folder_path && <span className="block text-xs text-slate-500">{folder.folder_path}</span>}
                        <SyncStatus lastSyncAt={folder.last_sync_at} />
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {folder.recursive && <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs rounded">Recursive</span>}
                        {folder.include_shared && <span className="px-2 py-0.5 bg-purple-100 text-purple-700 text-xs rounded">Shared</span>}
                        {folder.exclude_folder_ids.length > 0 && (
                          <span className="px-2 py-0.5 bg-amber-100 text-amber-700 text-xs rounded">
                            {folder.exclude_folder_ids.length} excluded
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <SyncButton
                          onSync={() => handleSyncFolder(account.id, folder.id)}
                          disabled={!folder.active || !account.active}
                          label="Sync"
                        />
                        {folder.recursive && (
                          <Button size="sm"
                            onClick={() => setManagingExclusionsFor({ accountId: account.id, folder })}
                          >
                            Exclusions
                          </Button>
                        )}
                        <Button size="sm"
                          onClick={() => handleToggleFolderActive(account.id, folder.id, folder.active)}
                        >
                          {folder.active ? 'Disable' : 'Enable'}
                        </Button>
                        <Button variant="danger" size="sm"
                          onClick={() => handleDeleteFolder(account.id, folder.id)}
                        >
                          Remove
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {addingFolderTo && (
        <GoogleFolderForm
          accountId={addingFolderTo}
          projects={projects}
          onSubmit={(data) => handleAddFolder(addingFolderTo, data)}
          onCancel={() => setAddingFolderTo(null)}
        />
      )}

      {browsingFoldersFor && (
        <FolderBrowser
          accountId={browsingFoldersFor}
          onSelect={(items) => handleBrowseSelect(browsingFoldersFor, items)}
          onCancel={() => setBrowsingFoldersFor(null)}
        />
      )}

      {managingExclusionsFor && (
        <ExclusionBrowser
          accountId={managingExclusionsFor.accountId}
          folder={managingExclusionsFor.folder}
          onSave={(excludeIds) => handleUpdateExclusions(managingExclusionsFor.accountId, managingExclusionsFor.folder.id, excludeIds)}
          onCancel={() => setManagingExclusionsFor(null)}
        />
      )}
    </Card>
  )
}

// === Google Drive Folder Browser ===

interface PathItem {
  id: string
  name: string
}

interface SelectedItem {
  id: string
  name: string
  is_folder: boolean
  recursive: boolean
}

interface FolderBrowserProps {
  accountId: number
  onSelect: (items: SelectedItem[]) => void
  onCancel: () => void
}

const FolderBrowser = ({ accountId, onSelect, onCancel }: FolderBrowserProps) => {
  const { browseGoogleDrive } = useSources()
  const [path, setPath] = useState<PathItem[]>([{ id: 'root', name: 'My Drive' }])
  const [items, setItems] = useState<DriveItem[]>([])
  const [selected, setSelected] = useState<Map<string, SelectedItem>>(new Map())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const currentFolderId = path[path.length - 1].id

  const loadFolder = useCallback(async (folderId: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await browseGoogleDrive(accountId, folderId)
      setItems(response.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load folder')
    } finally {
      setLoading(false)
    }
  }, [accountId, browseGoogleDrive])

  useEffect(() => {
    loadFolder(currentFolderId)
  }, [currentFolderId, loadFolder])

  const navigateToFolder = (item: DriveItem) => {
    setPath([...path, { id: item.id, name: item.name }])
  }

  const navigateToPathIndex = (index: number) => {
    setPath(path.slice(0, index + 1))
  }

  const toggleSelect = (item: DriveItem) => {
    const newSelected = new Map(selected)
    if (newSelected.has(item.id)) {
      newSelected.delete(item.id)
    } else {
      newSelected.set(item.id, {
        id: item.id,
        name: item.name,
        is_folder: item.is_folder,
        recursive: true,
      })
    }
    setSelected(newSelected)
  }

  const toggleRecursive = (itemId: string) => {
    const newSelected = new Map(selected)
    const item = newSelected.get(itemId)
    if (item) {
      newSelected.set(itemId, { ...item, recursive: !item.recursive })
      setSelected(newSelected)
    }
  }

  const handleAdd = () => {
    onSelect(Array.from(selected.values()))
  }

  return (
    <Modal title="Browse Google Drive" onClose={onCancel}>
      <div className="space-y-4">
        {/* Breadcrumb */}
        <div className="flex flex-wrap items-center gap-1 text-sm">
          {path.map((item, index) => (
            <span key={item.id} className="flex items-center gap-1">
              {index > 0 && <span className="text-slate-400">›</span>}
              <button
                className={cx(
                  'hover:underline',
                  index === path.length - 1 ? 'text-slate-800 font-medium' : 'text-primary'
                )}
                onClick={() => navigateToPathIndex(index)}
                disabled={index === path.length - 1}
              >
                {item.name}
              </button>
            </span>
          ))}
        </div>

        {/* Content */}
        {error && <Notice tone="error">{error}</Notice>}

        {loading ? (
          <div className="text-sm text-slate-500 py-8 text-center">Loading...</div>
        ) : items.length === 0 ? (
          <div className="text-sm text-slate-500 py-8 text-center">This folder is empty</div>
        ) : (
          <div className="max-h-80 overflow-y-auto border border-slate-200 rounded-lg">
            {items.map(item => (
              <div key={item.id} className={cx(
                'flex items-center gap-3 p-3 border-b border-slate-100 last:border-b-0',
                selected.has(item.id) && 'bg-primary/5'
              )}>
                <input
                  type="checkbox"
                  checked={selected.has(item.id)}
                  onChange={() => toggleSelect(item)}
                />
                <span className="text-lg">{item.is_folder ? '📁' : '📄'}</span>
                {item.id === 'shared' ? (
                  <span className="flex-1 text-slate-700">{item.name}</span>
                ) : (
                  <a
                    className="flex-1 text-slate-700 hover:text-primary"
                    href={item.is_folder
                      ? `https://drive.google.com/drive/folders/${item.id}`
                      : `https://drive.google.com/file/d/${item.id}`
                    }
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {item.name}
                  </a>
                )}
                {item.is_folder && selected.has(item.id) && (
                  <Checkbox checked={selected.get(item.id)?.recursive ?? true} onChange={() => toggleRecursive(item.id)} label="Recursive" />
                )}
                {item.is_folder && (
                  <Button size="sm"
                    onClick={() => navigateToFolder(item)}
                    title="Browse folder"
                  >
                    →
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between pt-2 border-t border-slate-100">
          <span className="text-sm text-slate-500">
            {selected.size} item{selected.size !== 1 ? 's' : ''} selected
          </span>
          <div className="flex gap-3">
            <Button type="button" onClick={onCancel}>Cancel</Button>
            <Button variant="primary"
              type="button"
              onClick={handleAdd}
              disabled={selected.size === 0}
            >
              Add Selected
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  )
}

// === Exclusion Browser ===

interface ExclusionBrowserProps {
  accountId: number
  folder: GoogleFolder
  onSave: (excludeIds: string[]) => void
  onCancel: () => void
}

interface ExcludedFolder {
  id: string
  name: string
  path: string
}

const ExclusionBrowser = ({ accountId, folder, onSave, onCancel }: ExclusionBrowserProps) => {
  const { browseGoogleDrive } = useSources()
  const [path, setPath] = useState<PathItem[]>([{ id: folder.folder_id, name: folder.folder_name }])
  const [items, setItems] = useState<DriveItem[]>([])
  const [excluded, setExcluded] = useState<Map<string, ExcludedFolder>>(() => {
    // Initialize with current exclusions (we don't have names, so use ID as name)
    const map = new Map<string, ExcludedFolder>()
    for (const id of folder.exclude_folder_ids) {
      map.set(id, { id, name: id, path: '(previously excluded)' })
    }
    return map
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const currentFolderId = path[path.length - 1].id
  const currentPath = path.map(p => p.name).join(' > ')

  const loadFolder = useCallback(async (folderId: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await browseGoogleDrive(accountId, folderId)
      // Only show folders, not files
      setItems(response.items.filter(item => item.is_folder))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load folder')
    } finally {
      setLoading(false)
    }
  }, [accountId, browseGoogleDrive])

  useEffect(() => {
    loadFolder(currentFolderId)
  }, [currentFolderId, loadFolder])

  const navigateToFolder = (item: DriveItem) => {
    setPath([...path, { id: item.id, name: item.name }])
  }

  const navigateToPathIndex = (index: number) => {
    setPath(path.slice(0, index + 1))
  }

  const toggleExclude = (item: DriveItem) => {
    const newExcluded = new Map(excluded)
    if (newExcluded.has(item.id)) {
      newExcluded.delete(item.id)
    } else {
      newExcluded.set(item.id, {
        id: item.id,
        name: item.name,
        path: currentPath + ' > ' + item.name,
      })
    }
    setExcluded(newExcluded)
  }

  const removeExclusion = (id: string) => {
    const newExcluded = new Map(excluded)
    newExcluded.delete(id)
    setExcluded(newExcluded)
  }

  const handleSave = () => {
    onSave(Array.from(excluded.keys()))
  }

  return (
    <Modal title={`Manage Exclusions: ${folder.folder_name}`} onClose={onCancel}>
      <div className="space-y-4">
        {/* Current exclusions */}
        {excluded.size > 0 && (
          <Notice tone="warning">
            <h5 className="text-sm font-medium text-amber-800 mb-2">Excluded Folders ({excluded.size})</h5>
            <div className="flex flex-wrap gap-2">
              {Array.from(excluded.values()).map(item => (
                <span key={item.id} className="inline-flex items-center gap-1 px-2 py-1 bg-white border border-amber-200 rounded text-sm" title={item.path}>
                  📁 {item.name}
                  <button
                    className="text-amber-600 hover:text-amber-800"
                    onClick={() => removeExclusion(item.id)}
                    title="Remove exclusion"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </Notice>
        )}

        {/* Breadcrumb */}
        <div className="flex flex-wrap items-center gap-1 text-sm">
          {path.map((item, index) => (
            <span key={item.id} className="flex items-center gap-1">
              {index > 0 && <span className="text-slate-400">›</span>}
              <button
                className={cx(
                  'hover:underline',
                  index === path.length - 1 ? 'text-slate-800 font-medium' : 'text-primary'
                )}
                onClick={() => navigateToPathIndex(index)}
                disabled={index === path.length - 1}
              >
                {item.name}
              </button>
            </span>
          ))}
        </div>

        {/* Content */}
        {error && <Notice tone="error">{error}</Notice>}

        {loading ? (
          <div className="text-sm text-slate-500 py-8 text-center">Loading...</div>
        ) : items.length === 0 ? (
          <div className="text-sm text-slate-500 py-8 text-center">No subfolders in this folder</div>
        ) : (
          <div className="max-h-60 overflow-y-auto border border-slate-200 rounded-lg">
            {items.map(item => (
              <div key={item.id} className={cx(
                'flex items-center gap-3 p-3 border-b border-slate-100 last:border-b-0',
                excluded.has(item.id) && 'bg-amber-50'
              )}>
                <input
                  type="checkbox"
                  checked={excluded.has(item.id)}
                  onChange={() => toggleExclude(item)}
                />
                <span className="text-lg">📁</span>
                <a
                  className="flex-1 text-slate-700 hover:text-primary"
                  href={`https://drive.google.com/drive/folders/${item.id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                >
                  {item.name}
                </a>
                {excluded.has(item.id) && (
                  <span className="px-2 py-0.5 bg-amber-200 text-amber-800 text-xs rounded">Excluded</span>
                )}
                <Button size="sm"
                  onClick={() => navigateToFolder(item)}
                  title="Browse subfolder"
                >
                  →
                </Button>
              </div>
            ))}
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between pt-2 border-t border-slate-100">
          <span className="text-sm text-slate-500">
            {excluded.size} folder{excluded.size !== 1 ? 's' : ''} excluded
          </span>
          <div className="flex gap-3">
            <Button type="button" onClick={onCancel}>Cancel</Button>
            <Button variant="primary"
              type="button"
              onClick={handleSave}
            >
              Save Exclusions
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  )
}

// === Google Folder Form (Add by ID) ===

interface GoogleFolderFormProps {
  accountId: number
  projects: Project[]
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const GoogleFolderForm = ({ accountId, projects, onSubmit, onCancel }: GoogleFolderFormProps) => {
  const [formData, setFormData] = useState({
    folder_id: '',
    folder_name: '',
    recursive: true,
    include_shared: false,
    tags: [] as string[],
    check_interval: 60,
    project_id: undefined as number | undefined,
    sensitivity: 'basic' as 'public' | 'basic' | 'internal' | 'confidential',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(formData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Add Google Drive Folder" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Folder ID" hint="Find this in the folder URL after /folders/">
          <Input
            type="text"
            value={formData.folder_id}
            onChange={e => setFormData({ ...formData, folder_id: e.target.value })}
            required
            placeholder="From Google Drive URL"
          />
        </Field>

        <Field label="Folder Name">
          <Input
            type="text"
            value={formData.folder_name}
            onChange={e => setFormData({ ...formData, folder_name: e.target.value })}
            required
            placeholder="My Documents"
          />
        </Field>

        <Field>
          <div className="flex flex-wrap gap-4">
            <Checkbox checked={formData.recursive} onChange={e => setFormData({ ...formData, recursive: e.target.checked })} label="Include subfolders" />
            <Checkbox checked={formData.include_shared} onChange={e => setFormData({ ...formData, include_shared: e.target.checked })} label="Include shared files" />
          </div>
        </Field>

        <IntervalInput
          value={formData.check_interval}
          onChange={check_interval => setFormData({ ...formData, check_interval })}
          label="Check interval"
        />

        <Field label="Tags">
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </Field>

        <FieldRow>
          <Field label="Project" hint="Project for access control">
            <Select
              value={formData.project_id || ''}
              onChange={e => setFormData({ ...formData, project_id: e.target.value ? parseInt(e.target.value) : undefined })}
            >
              <option value="">None</option>
              {projects.map(project => (
                <option key={project.id} value={project.id}>
                  {project.title} ({project.repo_path})
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Sensitivity" hint="Visibility level for files from this folder">
            <Select
              value={formData.sensitivity}
              onChange={e => setFormData({ ...formData, sensitivity: e.target.value as 'public' | 'basic' | 'internal' | 'confidential' })}
            >
              <option value="public">Public</option>
              <option value="basic">Basic</option>
              <option value="internal">Internal</option>
              <option value="confidential">Confidential</option>
            </Select>
          </Field>
        </FieldRow>

        <FormActions>
          <Button type="button" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={submitting}>
            {submitting ? 'Adding...' : 'Add Folder'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default GoogleDrivePanel
