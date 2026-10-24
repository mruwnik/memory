import { useState } from 'react'
import { Button, Field, FormActions, Input, Modal, requiredLabel } from '@/components/shared/ui'

/** The note's path relative to the notes folder, or the reason it can't be used. */
export const normalizeNotePath = (input: string, existing: string[]): { path: string } | { error: string } => {
  const trimmed = input.trim().replace(/^\/+/, '')
  if (!trimmed) return { error: 'Enter a file name.' }
  if (trimmed.endsWith('/')) return { error: `Add a file name after ${trimmed}` }
  if (trimmed.includes('\\')) return { error: 'Use / to separate folders.' }
  if (trimmed.split('/').some(part => part === '' || part === '.' || part === '..')) {
    return { error: 'The path can\'t contain empty, "." or ".." parts.' }
  }
  const path = trimmed.endsWith('.md') ? trimmed : `${trimmed}.md`
  return existing.includes(path) ? { error: `${path} already exists.` } : { path }
}

interface NewNoteModalProps {
  /** Fetches the current listing, so a stale tree can't let a new note overwrite an existing one. */
  fetchFiles: () => Promise<string[]>
  /** Folder the path starts in, so a note created while reading one lands beside it. */
  folder: string
  onCreate: (path: string, subject?: string) => void
  onCancel: () => void
}

export const NewNoteModal = ({ fetchFiles, folder, onCreate, onCancel }: NewNoteModalProps) => {
  const [path, setPath] = useState(folder ? `${folder}/` : '')
  const [subject, setSubject] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setChecking(true)
    try {
      const result = normalizeNotePath(path, await fetchFiles())
      if ('error' in result) return setError(result.error)
      onCreate(result.path, subject.trim() || undefined)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check existing notes')
    } finally {
      setChecking(false)
    }
  }

  return (
    <Modal title="New note" onClose={onCancel} size="md">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label={requiredLabel('Path')} error={error}
          hint="Relative to the notes folder, like work/ideas.md. Folders are created as needed; .md is added if missing.">
          <Input value={path} onChange={e => { setPath(e.target.value); setError(null) }} autoFocus required
            placeholder="work/ideas.md" spellCheck={false} />
        </Field>
        <Field label="Title" hint="Defaults to the file name.">
          <Input value={subject} onChange={e => setSubject(e.target.value)} />
        </Field>
        <FormActions>
          <Button type="button" onClick={onCancel}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={checking}>Create note</Button>
        </FormActions>
      </form>
    </Modal>
  )
}
