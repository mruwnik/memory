import { useState, useEffect, useCallback } from 'react'
import { useSources, GoogleAccount, GoogleFolder, DriveItem, LLMKeyChoice } from '@/hooks/useSources'
import { useModule } from '@/hooks/useModule'
import { useProjects, Project } from '@/hooks/useProjects'
import { ProjectField, IntervalInput, PauseButton, SensitivitySelect, Sensitivity, StatusBadge, SyncButton, SyncFailingBadge, SyncFailure, TagsInput, formatRelativeTime } from '../shared'
import { Badge, Button, Checkbox, ConfirmButton, EmptyState, ErrorState, Field, FieldRow, FormActions, Input, List, LoadingState, Modal, Notice, Row, RowError, Section, SubList, cx, useActionNotice, useSubmit, NewTabLink } from '@/components/shared/ui'
import { useSourcesContext } from '../context'
import { LLMKeyField } from '../LLMKeyField'

export const GoogleDrivePanel = () => {
  const {
    listGoogleAccounts,
    addGoogleFolder, updateGoogleFolder, deleteGoogleFolder, syncGoogleFolder
  } = useSources()
  const { listProjects } = useProjects()
  const teamsOn = useModule('teams')
  const { userId, refreshCounts } = useSourcesContext()
  const [accounts, setAccounts] = useState<GoogleAccount[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addingFolderTo, setAddingFolderTo] = useState<number | null>(null)
  const [browsingFoldersFor, setBrowsingFoldersFor] = useState<number | null>(null)
  const [editingFolder, setEditingFolder] = useState<{ accountId: number; folder: GoogleFolder } | null>(null)
  const [managingExclusionsFor, setManagingExclusionsFor] = useState<{ accountId: number; folder: GoogleFolder } | null>(null)
  const { run, notice } = useActionNotice()

  const loadData = useCallback(async () => {
    try {
      const [accountsData, projectsData] = await Promise.all([
        listGoogleAccounts(userId),
        (teamsOn ? listProjects() : Promise.resolve<Project[]>([]))
      ])
      setAccounts(accountsData)
      setProjects(projectsData)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
      refreshCounts()
    }
  }, [listGoogleAccounts, listProjects, teamsOn, userId, refreshCounts])

  useEffect(() => { loadData() }, [loadData])

  const handleAddFolder = async (accountId: number, data: any) => {
    await addGoogleFolder(accountId, data)
    setAddingFolderTo(null)
    loadData()
  }

  const handleEditFolder = async ({ accountId, folder }: { accountId: number; folder: GoogleFolder }, data: FolderFormData) => {
    const { folder_name, recursive, include_shared, tags, check_interval, project_id, sensitivity } = data
    await updateGoogleFolder(accountId, folder.id, { folder_name, recursive, include_shared, tags, check_interval, project_id: project_id ?? null, sensitivity })
    setEditingFolder(null)
    loadData()
  }

  // The browser modals close before saving, so the outcome shows in the section's notice.
  const handleBrowseSelect = async (accountId: number, items: SelectedItem[], access: FolderAccess) => {
    setBrowsingFoldersFor(null)
    await run(async () => {
      for (const item of items) {
        await addGoogleFolder(accountId, {
          folder_id: item.id,
          folder_name: item.name,
          recursive: item.is_folder ? item.recursive : false,
          ...access,
        })
      }
    })
    loadData()
  }

  const handleUpdateExclusions = async (accountId: number, folderId: number, excludeIds: string[]) => {
    setManagingExclusionsFor(null)
    await run(() => updateGoogleFolder(accountId, folderId, { exclude_folder_ids: excludeIds }))
    loadData()
  }

  const handleDeleteFolder = (accountId: number, folderId: number) => run(async () => {
    await deleteGoogleFolder(accountId, folderId)
    loadData()
  })

  const handleToggleFolderActive = (accountId: number, folderId: number, active: boolean) => run(async () => {
    await updateGoogleFolder(accountId, folderId, { active: !active })
    loadData()
  })

  // One folder's LLM key saves at a time; its failure shows in that folder's row.
  const folderLLM = useSubmit()
  const [llmFolderId, setLlmFolderId] = useState<number>()
  const handleFolderLLMKey = (accountId: number, folderId: number, llm_key: LLMKeyChoice) => {
    setLlmFolderId(folderId)
    return folderLLM.submit(async () => {
      await updateGoogleFolder(accountId, folderId, { llm_key })
      loadData()
    })
  }

  const handleSyncFolder = (accountId: number, folderId: number) => run(async () => {
    await syncGoogleFolder(accountId, folderId)
    loadData()
  }, 'Sync queued')

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <Section title="Google Drive folders">
      {notice}
      {accounts.length === 0 ? (
        <EmptyState message="No Google accounts connected. Connect a Google account first." />
      ) : (
        <List>
          {accounts.map(account => (
            <Row
              key={account.id}
              title={account.email}
              muted={!account.active}
              badges={<><StatusBadge active={account.active} /><SyncFailingBadge status={account} /></>}
            >
              <SyncFailure status={account} />
              <SubList
                title={`Synced folders (${account.folders.length})`}
                actions={<>
                  <Button size="sm" onClick={() => setBrowsingFoldersFor(account.id)}>Browse & add</Button>
                  <Button size="sm" onClick={() => setAddingFolderTo(account.id)}>Add by ID</Button>
                </>}
                empty="No folders configured for sync"
              >
                  {account.folders.map(folder => (
                    <Row
                      key={folder.id}
                      muted={!folder.active}
                      title={
                        <NewTabLink className="text-primary hover:underline" href={`https://drive.google.com/open?id=${folder.folder_id}`}>
                          {folder.folder_name}
                        </NewTabLink>
                      }
                      badges={<>
                        <StatusBadge active={folder.active} />
                        {folder.recursive && <Badge tone="info">Recursive</Badge>}
                        {folder.include_shared && <Badge tone="primary">Shared</Badge>}
                        {folder.exclude_folder_ids.length > 0 && <Badge tone="warning">{folder.exclude_folder_ids.length} excluded</Badge>}
                        <SyncFailingBadge status={folder} />
                      </>}
                      meta={[folder.folder_path, `Last sync: ${formatRelativeTime(folder.last_sync_at)}`]}
                      actions={<>
                        <LLMKeyField compact value={folder.llm_key} onChange={llm_key => handleFolderLLMKey(account.id, folder.id, llm_key)} />
                        <SyncButton onSync={() => handleSyncFolder(account.id, folder.id)} disabled={!folder.active || !account.active} label="Sync" />
                        {folder.recursive && (
                          <Button size="sm" onClick={() => setManagingExclusionsFor({ accountId: account.id, folder })}>Exclusions</Button>
                        )}
                        <PauseButton active={folder.active} onClick={() => handleToggleFolderActive(account.id, folder.id, folder.active)} />
                        <Button size="sm" onClick={() => setEditingFolder({ accountId: account.id, folder })}>Edit</Button>
                        <ConfirmButton confirm={`Remove "${folder.folder_name}" from sync?`} onConfirm={() => handleDeleteFolder(account.id, folder.id)}>
                          Remove
                        </ConfirmButton>
                      </>}
                    >
                      {llmFolderId === folder.id && folderLLM.error && <RowError live>{folderLLM.error}</RowError>}
                      <SyncFailure status={folder} />
                    </Row>
                  ))}
              </SubList>
            </Row>
          ))}
        </List>
      )}

      {addingFolderTo && (
        <GoogleFolderForm
          projects={projects}
          onSubmit={(data) => handleAddFolder(addingFolderTo, data)}
          onCancel={() => setAddingFolderTo(null)}
        />
      )}

      {editingFolder && (
        <GoogleFolderForm
          folder={editingFolder.folder}
          projects={projects}
          onSubmit={(data) => handleEditFolder(editingFolder, data)}
          onCancel={() => setEditingFolder(null)}
        />
      )}

      {browsingFoldersFor && (
        <FolderBrowser
          accountId={browsingFoldersFor}
          projects={projects}
          onSelect={(items, access) => handleBrowseSelect(browsingFoldersFor, items, access)}
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
    </Section>
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

interface FolderAccess {
  project_id?: number
  sensitivity: Sensitivity
  llm_key?: LLMKeyChoice
}

const Breadcrumb = ({ path, onNavigate }: { path: PathItem[]; onNavigate: (index: number) => void }) => (
  <nav aria-label="Folder path">
    <ol className="flex flex-wrap items-center gap-1 text-sm">
      {path.map((item, index) => (
        <li key={item.id} className="flex items-center gap-1">
          {index > 0 && <span aria-hidden="true" className="text-slate-500">›</span>}
          {index === path.length - 1
            ? <span aria-current="location" className="text-slate-800 font-medium">{item.name}</span>
            : <button type="button" className="text-primary hover:underline" onClick={() => onNavigate(index)}>{item.name}</button>}
        </li>
      ))}
    </ol>
  </nav>
)

interface FolderBrowserProps {
  accountId: number
  projects: Project[]
  onSelect: (items: SelectedItem[], access: FolderAccess) => void
  onCancel: () => void
}

const FolderBrowser = ({ accountId, projects, onSelect, onCancel }: FolderBrowserProps) => {
  const { browseGoogleDrive } = useSources()
  const [path, setPath] = useState<PathItem[]>([{ id: 'root', name: 'My Drive' }])
  const [items, setItems] = useState<DriveItem[]>([])
  const [selected, setSelected] = useState<Map<string, SelectedItem>>(new Map())
  const [access, setAccess] = useState<FolderAccess>({ sensitivity: 'basic' })
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
    onSelect(Array.from(selected.values()), access)
  }

  return (
    <Modal title="Browse Google Drive" onClose={onCancel}>
      <div className="space-y-4">
        <Breadcrumb path={path} onNavigate={navigateToPathIndex} />

        {error && <Notice tone="error">{error}</Notice>}

        {loading ? (
          <LoadingState />
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
                  aria-label={item.name}
                  checked={selected.has(item.id)}
                  onChange={() => toggleSelect(item)}
                />
                <span aria-hidden="true" className="text-lg">{item.is_folder ? '📁' : '📄'}</span>
                {item.id === 'shared' ? (
                  <span className="flex-1 text-slate-700">{item.name}</span>
                ) : (
                  <NewTabLink
                    className="flex-1 text-slate-700 hover:text-primary"
                    href={item.is_folder
                      ? `https://drive.google.com/drive/folders/${item.id}`
                      : `https://drive.google.com/file/d/${item.id}`
                    }
                    onClick={(e) => e.stopPropagation()}
                  >
                    {item.name}
                  </NewTabLink>
                )}
                {item.is_folder && selected.has(item.id) && (
                  <Checkbox checked={selected.get(item.id)?.recursive ?? true} onChange={() => toggleRecursive(item.id)} label="Recursive" />
                )}
                {item.is_folder && (
                  <Button size="sm"
                    onClick={() => navigateToFolder(item)}
                    aria-label={`Open folder ${item.name}`}
                  >
                    →
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}

        <FieldRow>
          <ProjectField hint="Project for access control" value={access.project_id} projects={projects}
            onChange={project_id => setAccess({ ...access, project_id })} />
          <Field label="Sensitivity" hint="Visibility level for files from the added folders">
            <SensitivitySelect value={access.sensitivity} onChange={sensitivity => setAccess({ ...access, sensitivity })} />
          </Field>
        </FieldRow>

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
              Add selected
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
      const folders = response.items.filter(item => item.is_folder)
      setItems(folders)
      // Saved exclusions are bare IDs; name them once browsing reaches them.
      setExcluded(prev => new Map([...prev].map(([id, ex]) => {
        const seen = ex.name === id && folders.find(f => f.id === id)
        return [id, seen ? { ...ex, name: seen.name } : ex]
      })))
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
    <Modal title={`Manage exclusions: ${folder.folder_name}`} onClose={onCancel}>
      <div className="space-y-4">
        {/* Current exclusions */}
        {excluded.size > 0 && (
          <Notice tone="warning">
            <h5 className="text-sm font-medium text-amber-800 mb-2">Excluded folders ({excluded.size})</h5>
            <div className="flex flex-wrap gap-2">
              {Array.from(excluded.values()).map(item => (
                <span key={item.id} className="inline-flex items-center gap-1 px-2 py-1 bg-white border border-amber-200 rounded text-sm" title={item.path}>
                  <span aria-hidden="true">📁</span> {item.name}
                  <button
                    type="button"
                    className="text-amber-600 hover:text-amber-800"
                    onClick={() => removeExclusion(item.id)}
                    aria-label={`Remove exclusion ${item.name}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </Notice>
        )}

        <Breadcrumb path={path} onNavigate={navigateToPathIndex} />

        {error && <Notice tone="error">{error}</Notice>}

        {loading ? (
          <LoadingState />
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
                  aria-label={item.name}
                  checked={excluded.has(item.id)}
                  onChange={() => toggleExclude(item)}
                />
                <span aria-hidden="true" className="text-lg">📁</span>
                <NewTabLink
                  className="flex-1 text-slate-700 hover:text-primary"
                  href={`https://drive.google.com/drive/folders/${item.id}`}
                  onClick={(e) => e.stopPropagation()}
                >
                  {item.name}
                </NewTabLink>
                {excluded.has(item.id) && (
                  <span className="px-2 py-0.5 bg-amber-200 text-amber-800 text-xs rounded">Excluded</span>
                )}
                <Button size="sm"
                  onClick={() => navigateToFolder(item)}
                  aria-label={`Open folder ${item.name}`}
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
              Save exclusions
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  )
}

// === Google Folder Form (add by ID, or edit) ===

interface FolderFormData {
  folder_id: string
  folder_name: string
  recursive: boolean
  include_shared: boolean
  tags: string[]
  check_interval: number
  project_id?: number
  sensitivity: Sensitivity
}

interface GoogleFolderFormProps {
  /** The folder to edit; omitted to add one by ID. */
  folder?: GoogleFolder
  projects: Project[]
  onSubmit: (data: FolderFormData) => Promise<void>
  onCancel: () => void
}

const GoogleFolderForm = ({ folder, projects, onSubmit, onCancel }: GoogleFolderFormProps) => {
  const [formData, setFormData] = useState<FolderFormData>({
    folder_id: folder?.folder_id ?? '',
    folder_name: folder?.folder_name ?? '',
    recursive: folder?.recursive ?? true,
    include_shared: folder?.include_shared ?? false,
    tags: folder?.tags ?? [],
    check_interval: folder?.check_interval ?? 60,
    project_id: folder?.project_id ?? undefined,
    sensitivity: folder?.sensitivity ?? 'basic',
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
    <Modal title={folder ? 'Edit folder' : 'Add Google Drive folder'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        {!folder && (
          <Field label="Folder ID" hint="Find this in the folder URL after /folders/">
            <Input
              type="text"
              value={formData.folder_id}
              onChange={e => setFormData({ ...formData, folder_id: e.target.value })}
              required
              placeholder="From Google Drive URL"
            />
          </Field>
        )}

        <Field label="Folder name">
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
          <ProjectField hint="Project for access control" value={formData.project_id} projects={projects}
            onChange={project_id => setFormData({ ...formData, project_id })} />
          <Field label="Sensitivity" hint="Visibility level for files from this folder">
            <SensitivitySelect value={formData.sensitivity} onChange={sensitivity => setFormData({ ...formData, sensitivity })} />
          </Field>
        </FieldRow>

        {!folder && <LLMKeyField value={undefined} onChange={llm_key => setFormData({ ...formData, llm_key })} />}

        <FormActions>
          <Button type="button" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={submitting}>
            {folder ? (submitting ? 'Saving...' : 'Save') : (submitting ? 'Adding...' : 'Add folder')}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default GoogleDrivePanel
