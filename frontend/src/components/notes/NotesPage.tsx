import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMCP } from '@/hooks/useMCP'
import { Button, ConfirmDialog, EmptyState, ErrorState, LoadingState, Page, PaneHeader, SplitView, Textarea, ToggleGroup, useActionNotice, useUnsavedGuard } from '@/components/shared/ui'
import { Markdown } from '@/components/shared/Markdown'
import { NewNoteModal } from './NewNoteModal'

interface TreeNode {
  files: string[]
  folders: Record<string, TreeNode>
}

const buildTree = (paths: string[]): TreeNode => {
  const root: TreeNode = { files: [], folders: {} }

  for (const path of paths) {
    const parts = path.split('/')
    const fileName = parts.pop()!
    let current = root

    for (const part of parts) {
      if (!current.folders[part]) {
        current.folders[part] = { files: [], folders: {} }
      }
      current = current.folders[part]
    }

    current.files.push(fileName)
  }

  return root
}

interface FolderNodeProps {
  name: string
  node: TreeNode
  path: string
  expandedFolders: Set<string>
  selectedFile: string | null
  onToggleFolder: (path: string) => void
  onSelectFile: (path: string) => void
  depth: number
}

const FolderNode = ({
  name,
  node,
  path,
  expandedFolders,
  selectedFile,
  onToggleFolder,
  onSelectFile,
  depth,
}: FolderNodeProps) => {
  const isExpanded = expandedFolders.has(path)
  const folderNames = Object.keys(node.folders).sort()
  const fileNames = [...node.files].sort()
  const paddingLeft = depth * 16

  return (
    <div>
      {name && (
        <button
          onClick={() => onToggleFolder(path)}
          aria-expanded={isExpanded}
          className="w-full text-left px-3 py-1.5 hover:bg-slate-100 flex items-center gap-2 text-sm"
          style={{ paddingLeft: `${paddingLeft + 12}px` }}
        >
          <span aria-hidden="true" className="text-slate-500 w-4">
            {isExpanded ? '▼' : '▶'}
          </span>
          <span aria-hidden="true" className="text-amber-600">📁</span>
          <span className="text-slate-700 font-medium">{name}</span>
        </button>
      )}

      {(isExpanded || !name) && (
        <div>
          {folderNames.map((folderName) => (
            <FolderNode
              key={folderName}
              name={folderName}
              node={node.folders[folderName]}
              path={path ? `${path}/${folderName}` : folderName}
              expandedFolders={expandedFolders}
              selectedFile={selectedFile}
              onToggleFolder={onToggleFolder}
              onSelectFile={onSelectFile}
              depth={name ? depth + 1 : depth}
            />
          ))}

          {fileNames.map((fileName) => {
            const filePath = path ? `${path}/${fileName}` : fileName
            const isSelected = selectedFile === filePath
            return (
              <button
                key={fileName}
                onClick={() => onSelectFile(filePath)}
                aria-current={isSelected || undefined}
                className={`w-full text-left px-3 py-1.5 flex items-center gap-2 text-sm ${
                  isSelected
                    ? 'bg-primary/10 text-primary-dark'
                    : 'hover:bg-slate-100 text-slate-600'
                }`}
                style={{ paddingLeft: `${(name ? depth + 1 : depth) * 16 + 12 + 16}px` }}
              >
                <span aria-hidden="true" className="text-slate-500">📄</span>
                <span>{fileName}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export const NotesPage = () => {
  const { listNotes, fetchFile, saveNote } = useMCP()
  const [searchParams, setSearchParams] = useSearchParams()
  const [files, setFiles] = useState<string[]>([])
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<string | null>(null)
  const [editedContent, setEditedContent] = useState<string | null>(null)
  const [isEditing, setIsEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingContent, setLoadingContent] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [contentError, setContentError] = useState<string | null>(null)
  const { run, notice, clear: clearNotice } = useActionNotice()
  const requestedFileRef = useRef<string | null>(null)
  const reloadTimerRef = useRef<ReturnType<typeof setTimeout>>()
  // Where our own navigation is taking ?file= (null: no file). Navigations commit in a transition, so until
  // it lands the URL still names the old file, which must not be taken as a Back/link to it.
  const pendingUrlFileRef = useRef<string | null | undefined>(undefined)
  const setUrlFile = (path: string | null, replace = false) => {
    if (path === fileFromUrl) return
    pendingUrlFileRef.current = path
    setSearchParams(path ? { file: path } : {}, { replace })
  }
  const [creating, setCreating] = useState(false)
  const [draftSubject, setDraftSubject] = useState<string | undefined>()
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null)

  const hasChanges = editedContent !== null && editedContent !== fileContent
  const leaveFile = useCallback((go: () => void) => hasChanges ? setPendingLeave(() => go) : go(), [hasChanges])

  const discardMessage = `Discard unsaved changes to ${selectedFile}?`
  const { dialog: leaveDialog } = useUnsavedGuard(hasChanges, discardMessage)

  // Get file path from URL
  const fileFromUrl = searchParams.get('file')

  const fetchFileList = useCallback(async (): Promise<string[]> => {
    const result = await listNotes('/')
    // Result is an array with the file list as first element
    const fileList = Array.isArray(result) ? result[0] : result
    // Strip /notes/ prefix from paths for cleaner tree display
    return (Array.isArray(fileList) ? fileList : []).map((p: string) => p.replace(/^\/notes\//, ''))
  }, [listNotes])

  const loadFiles = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true)
      const cleanPaths = await fetchFileList()
      // A silent reload only reconciles after a save; the write may not have landed yet, so keep what is shown.
      setFiles(prev => silent ? [...new Set([...prev, ...cleanPaths])] : cleanPaths)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load notes')
    } finally {
      setLoading(false)
    }
  }, [fetchFileList])

  const loadFileContent = useCallback(async (path: string) => {
    requestedFileRef.current = path
    clearNotice()
    try {
      setLoadingContent(true)
      setFileContent(null)
      setContentError(null)
      setEditedContent(null)
      setIsEditing(false)
      // Add /notes/ prefix back for the API call
      const result = await fetchFile(`/notes/${path}`)
      if (requestedFileRef.current !== path) return
      // Result structure: [{ content: [{ type, mime_type, data }] }]
      // For text files, all chunks are joined together
      const fileData = result?.[0]
      let content: string
      if (fileData?.content && Array.isArray(fileData.content)) {
        const textParts = fileData.content
          .filter((c: { type: string }) => c.type === 'text')
          .map((c: { data: string }) => c.data)
        content = textParts.join('\n\n')
      } else if (typeof result?.[0] === 'string') {
        content = result[0]
      } else {
        content = JSON.stringify(result, null, 2)
      }
      setFileContent(content)
      setEditedContent(content)
    } catch (e) {
      if (requestedFileRef.current !== path) return
      setContentError(e instanceof Error ? e.message : 'Failed to load note')
    } finally {
      if (requestedFileRef.current === path) setLoadingContent(false)
    }
  }, [fetchFile, clearNotice])

  const handleSave = useCallback(async () => {
    if (!selectedFile || editedContent === null) return
    setSaving(true)
    // selectedFile is already clean (no /notes/ prefix)
    const isNew = !files.includes(selectedFile)
    const saved = await run(() => saveNote(selectedFile, editedContent, draftSubject, isNew))
    setSaving(false)
    if (!saved) return
    setFileContent(editedContent)
    setIsEditing(false)
    setDraftSubject(undefined)
    if (!isNew) return
    setFiles(prev => [...prev, selectedFile])
    // The note is written by a background task, so the listing is reconciled a moment later.
    clearTimeout(reloadTimerRef.current)
    reloadTimerRef.current = setTimeout(() => loadFiles(true), 1000)
  }, [selectedFile, editedContent, draftSubject, files, saveNote, run, loadFiles])

  useEffect(() => () => clearTimeout(reloadTimerRef.current), [])

  useEffect(() => {
    loadFiles()
  }, [loadFiles])

  const expandParents = (path: string) => {
    const parents = path.split('/').slice(0, -1).map((_, i, parts) => parts.slice(0, i + 1).join('/'))
    setExpandedFolders(prev => new Set([...prev, ...parents]))
  }

  // Back/Forward and links change ?file= without a click in the tree, so they go through the same discard check.
  useEffect(() => {
    if (pendingUrlFileRef.current !== undefined) {
      if (fileFromUrl === pendingUrlFileRef.current) pendingUrlFileRef.current = undefined
      return
    }
    if (!fileFromUrl || fileFromUrl === selectedFile || !files.includes(fileFromUrl)) return
    leaveFile(() => {
      setSelectedFile(fileFromUrl)
      loadFileContent(fileFromUrl)
      expandParents(fileFromUrl)
    })
  }, [fileFromUrl, files, selectedFile, loadFileContent, leaveFile])

  const handleToggleFolder = (path: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev)
      if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
      }
      return next
    })
  }

  const handleSelectFile = (path: string) => leaveFile(() => {
    setSelectedFile(path)
    loadFileContent(path)
    setUrlFile(path)
  })

  const handleCreateNote = (path: string, subject?: string) => {
    setCreating(false)
    leaveFile(() => startDraft(path, subject))
  }

  const startDraft = (path: string, subject?: string) => {
    requestedFileRef.current = path
    clearNotice()
    setContentError(null)
    setLoadingContent(false)
    setSelectedFile(path)
    setFileContent('')
    setEditedContent('')
    setIsEditing(true)
    setDraftSubject(subject)
    expandParents(path)
    setUrlFile(path)
  }

  const tree = buildTree(files)

  return (
    <Page title="Notes" width="full" fill actions={<Button variant="primary" onClick={() => setCreating(true)}>New note</Button>}>
      <SplitView
        listTitle="Files"
        list={loading ? <LoadingState />
          : error ? <ErrorState message={error} onRetry={() => loadFiles()} />
          : files.length === 0 ? <div className="p-4"><EmptyState message="No notes found" /></div>
          : (
            <FolderNode
              name=""
              node={tree}
              path=""
              expandedFolders={expandedFolders}
              selectedFile={selectedFile}
              onToggleFolder={handleToggleFolder}
              onSelectFile={handleSelectFile}
              depth={0}
            />
          )}
        detail={selectedFile && (
          <>
            <PaneHeader
              title={selectedFile}
              actions={fileContent !== null && <>
                {hasChanges && <span className="text-xs text-amber-800">Unsaved changes</span>}
                <ToggleGroup label="View mode" value={isEditing ? 'edit' : 'preview'} onChange={v => setIsEditing(v === 'edit')}
                  options={[{ value: 'preview', label: 'Preview' }, { value: 'edit', label: 'Edit' }]} />
                <Button variant="primary" size="sm" onClick={handleSave} disabled={!hasChanges || saving}>
                  {saving ? 'Saving...' : 'Save'}
                </Button>
              </>}
            />
            <div className="flex-1 flex flex-col gap-3 overflow-hidden p-4">
              {notice}
              {loadingContent ? (
                <LoadingState message="Loading content..." />
              ) : contentError ? (
                <ErrorState message={contentError} onRetry={() => loadFileContent(selectedFile)} />
              ) : isEditing ? (
                <Textarea
                  value={editedContent ?? ''}
                  onChange={(e) => setEditedContent(e.target.value)}
                  className="flex-1 font-mono leading-relaxed"
                  aria-label="Note content"
                  spellCheck={false}
                />
              ) : (
                <div className="flex-1 overflow-y-auto">
                  <Markdown className="text-slate-700">{editedContent ?? ''}</Markdown>
                </div>
              )}
            </div>
          </>
        )}
        placeholder="Select a file to view its content"
        onBack={() => leaveFile(() => { setSelectedFile(null); setUrlFile(null) })}
        backLabel="Files"
      />
      {creating && (
        <NewNoteModal fetchFiles={fetchFileList} folder={selectedFile?.split('/').slice(0, -1).join('/') ?? ''}
          onCreate={handleCreateNote} onCancel={() => setCreating(false)} />
      )}
      {leaveDialog}
      {pendingLeave && (
        <ConfirmDialog message={discardMessage} confirmLabel="Discard"
          onCancel={() => { setPendingLeave(null); setUrlFile(selectedFile, true) }}
          onConfirm={() => { setPendingLeave(null); pendingLeave() }} />
      )}
    </Page>
  )
}
