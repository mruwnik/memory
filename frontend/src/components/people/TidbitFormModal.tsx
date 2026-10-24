import { useState } from 'react'
import type { Tidbit, TidbitInput } from '../../hooks/usePeople'
import { Button, Field, FieldRow, FormActions, Input, Modal, Notice, Select, Textarea, requiredLabel } from '@/components/shared/ui'

const SENSITIVITIES = ['public', 'basic', 'internal', 'confidential']

interface TidbitFormModalProps {
  title: string
  initialData?: Tidbit
  onSubmit: (data: TidbitInput) => Promise<void>
  onCancel: () => void
  loading: boolean
  error: string | null
  submitLabel: string
}

const TidbitFormModal = ({ title, initialData, onSubmit, onCancel, loading, error, submitLabel }: TidbitFormModalProps) => {
  const [content, setContent] = useState(initialData?.content ?? '')
  const [type, setType] = useState(initialData?.tidbit_type ?? 'note')
  const [tags, setTags] = useState(initialData?.tags?.join(', ') ?? '')
  const [sensitivity, setSensitivity] = useState(initialData?.sensitivity ?? 'basic')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSubmit({
      content,
      tidbit_type: type.trim() || initialData?.tidbit_type || 'note',
      tags: tags.split(',').map(t => t.trim()).filter(Boolean),
      sensitivity,
    })
  }

  return (
    <Modal title={title} onClose={onCancel}>
      {error && <Notice tone="error" role="alert" className="mb-4">{error}</Notice>}
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label={requiredLabel('Content')}>
          <Textarea value={content} onChange={e => setContent(e.target.value)} rows={5} required autoFocus
            placeholder="e.g., Prefers morning meetings" />
        </Field>
        <FieldRow>
          <Field label="Type" hint="note, preference, fact, ...">
            <Input value={type} onChange={e => setType(e.target.value)} />
          </Field>
          <Field label="Sensitivity">
            <Select value={sensitivity} onChange={e => setSensitivity(e.target.value)}>
              {SENSITIVITIES.map(level => <option key={level} value={level}>{level}</option>)}
            </Select>
          </Field>
        </FieldRow>
        {!initialData && <Notice tone="info">Only visible to you.</Notice>}
        <Field label="Tags" hint="Comma-separated">
          <Input value={tags} onChange={e => setTags(e.target.value)} placeholder="e.g., scheduling, dietary" />
        </Field>
        <FormActions>
          <Button type="button" onClick={onCancel}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={loading}>{loading ? 'Saving...' : submitLabel}</Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default TidbitFormModal
