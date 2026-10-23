import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMCP } from '@/hooks/useMCP'
import { Button, ErrorState, LoadingState, Page, Textarea, ToggleGroup } from '@/components/shared/ui'
import { Markdown } from '@/components/shared/Markdown'

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
          className="w-full text-left px-3 py-1.5 hover:bg-slate-100 flex items-center gap-2 text-sm"
          style={{ paddingLeft: `${paddingLeft + 12}px` }}
        >
          <span className="text-slate-400 w-4">
            {isExpanded ? '▼' : '▶'}
          </span>
          <span className="text-amber-600">📁</span>
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
                className={`w-full text-left px-3 py-1.5 flex items-center gap-2 text-sm ${
                  isSelected
                    ? 'bg-primary/10 text-primary'
                    : 'hover:bg-slate-100 text-slate-600'
                }`}
                style={{ paddingLeft: `${(name ? depth + 1 : depth) * 16 + 12 + 16}px` }}
              >
                <span className="text-slate-400">📄</span>
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

  const hasChanges = editedContent !== null && editedContent !== fileContent

  // Get file path from URL
  const fileFromUrl = searchParams.get('file')

  const loadFiles = useCallback(async () => {
    try {
      setLoading(true)
      const result = await listNotes('/')
      // Result is an array with the file list as first element
      const fileList = Array.isArray(result) ? result[0] : result
      // Strip /notes/ prefix from paths for cleaner tree display
      const cleanPaths = (Array.isArray(fileList) ? fileList : [])
        .map((p: string) => p.replace(/^\/notes\//, ''))
      setFiles(cleanPaths)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load notes')
    } finally {
      setLoading(false)
    }
  }, [listNotes])

  const loadFileContent = useCallback(async (path: string) => {
    try {
      setLoadingContent(true)
      setEditedContent(null)
      setIsEditing(false)
      // Add /notes/ prefix back for the API call
      const result = await fetchFile(`/notes/${path}`)
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
      setFileContent(`Error loading file: ${e instanceof Error ? e.message : 'Unknown error'}`)
    } finally {
      setLoadingContent(false)
    }
  }, [fetchFile])

  const handleSave = useCallback(async () => {
    if (!selectedFile || editedContent === null) return
    try {
      setSaving(true)
      // selectedFile is already clean (no /notes/ prefix)
      await saveNote(selectedFile, editedContent)
      setFileContent(editedContent)
      setIsEditing(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }, [selectedFile, editedContent, saveNote])

  useEffect(() => {
    loadFiles()
  }, [loadFiles])

  // Sync URL -> state: when URL changes or files load, select the file from URL
  useEffect(() => {
    if (fileFromUrl && files.length > 0 && fileFromUrl !== selectedFile) {
      // Check if file exists in our list
      if (files.includes(fileFromUrl)) {
        setSelectedFile(fileFromUrl)
        loadFileContent(fileFromUrl)
        // Expand parent folders
        const parts = fileFromUrl.split('/')
        const folders: string[] = []
        for (let i = 0; i < parts.length - 1; i++) {
          folders.push(parts.slice(0, i + 1).join('/'))
        }
        setExpandedFolders(prev => {
          const next = new Set(prev)
          folders.forEach(f => next.add(f))
          return next
        })
      }
    }
  }, [fileFromUrl, files, selectedFile, loadFileContent])

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

  const handleSelectFile = (path: string) => {
    setSelectedFile(path)
    loadFileContent(path)
    // Update URL
    setSearchParams({ file: path })
  }

  const tree = buildTree(files)

  return (
    <Page title="Notes" width="full" fill>
      <div className="h-full flex overflow-hidden bg-white rounded-xl border border-slate-200 shadow-sm">
        {/* File Browser Panel */}
        <div className="w-80 shrink-0 border-r border-slate-200 flex flex-col">
          <div className="px-4 py-3 border-b border-slate-200">
            <h2 className="text-sm font-semibold text-slate-700">Files</h2>
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <LoadingState />
            ) : error ? (
              <ErrorState message={error} />
            ) : files.length === 0 ? (
              <div className="p-4 text-center text-slate-600">No notes found</div>
            ) : (
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
          </div>
        </div>

        {/* Content Panel */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {selectedFile ? (
            <>
              <div className="px-6 py-3 border-b border-slate-200 flex flex-wrap gap-2 items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-700">{selectedFile}</h2>
                <div className="flex items-center gap-3">
                  {hasChanges && (
                    <span className="text-xs text-amber-700">Unsaved changes</span>
                  )}
                  <ToggleGroup label="View mode" value={isEditing ? 'edit' : 'preview'} onChange={v => setIsEditing(v === 'edit')}
                    options={[{ value: 'preview', label: 'Preview' }, { value: 'edit', label: 'Edit' }]} />
                  <Button variant="primary" size="sm" onClick={handleSave} disabled={!hasChanges || saving}>
                    {saving ? 'Saving...' : 'Save'}
                  </Button>
                </div>
              </div>
              <div className="flex-1 flex flex-col overflow-hidden p-4">
                {loadingContent ? (
                  <LoadingState message="Loading content..." />
                ) : isEditing ? (
                  <Textarea
                    value={editedContent ?? ''}
                    onChange={(e) => setEditedContent(e.target.value)}
                    className="flex-1 font-mono leading-relaxed"
                    spellCheck={false}
                  />
                ) : (
                  <div className="flex-1 overflow-y-auto bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                    <Markdown className="text-slate-700">{editedContent ?? ''}</Markdown>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-slate-600">
              Select a file to view its content
            </div>
          )}
        </div>
      </div>
    </Page>
  )
}
