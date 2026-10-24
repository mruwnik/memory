import { useState } from 'react'
import type { Person, PersonCreate, PersonUpdate } from '../../hooks/usePeople'
import { Button, Input, Modal, Notice, Textarea, Field, FieldRow, Fieldset, FormActions, requiredLabel } from '@/components/shared/ui'

interface PersonFormModalProps {
  title: string
  initialData?: Person
  onSubmit: (data: PersonCreate | PersonUpdate) => Promise<void>
  onCancel: () => void
  loading: boolean
  error: string | null
  submitLabel: string
  isEdit?: boolean
}

interface FormData {
  identifier: string
  display_name: string
  aliases: string
  tags: string
  notes: string
  // Contact info fields
  email: string
  phone: string
  github: string
  twitter: string
  website: string
}

const CONTACT_FIELDS = [
  { key: 'email', label: 'Email', type: 'email', placeholder: 'alice@example.com' },
  { key: 'phone', label: 'Phone', type: 'tel', placeholder: '+1 555-1234' },
  { key: 'github', label: 'GitHub', type: 'text', placeholder: '@alicechen' },
  { key: 'twitter', label: 'Twitter/X', type: 'text', placeholder: '@alice_c' },
  { key: 'website', label: 'Website', type: 'url', placeholder: 'https://alice.dev' },
] as const

const PersonFormModal = ({
  title,
  initialData,
  onSubmit,
  onCancel,
  loading,
  error,
  submitLabel,
  isEdit = false,
}: PersonFormModalProps) => {
  const [form, setForm] = useState<FormData>({
    identifier: initialData?.identifier || '',
    display_name: initialData?.display_name || '',
    aliases: initialData?.aliases?.join(', ') || '',
    tags: initialData?.tags?.join(', ') || '',
    notes: '',
    email: initialData?.contact_info?.email || '',
    phone: initialData?.contact_info?.phone || '',
    github: initialData?.contact_info?.github || '',
    twitter: initialData?.contact_info?.twitter || '',
    website: initialData?.contact_info?.website || '',
  })

  const generateIdentifier = (name: string): string => {
    return name
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '') // Remove diacritics
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, '')
      .replace(/\s+/g, '_')
      .substring(0, 50)
  }

  const handleDisplayNameChange = (value: string) => {
    const updates: Partial<FormData> = { display_name: value }
    // Auto-generate identifier if creating new and identifier is empty or was auto-generated
    if (!isEdit && (!form.identifier || form.identifier === generateIdentifier(form.display_name))) {
      updates.identifier = generateIdentifier(value)
    }
    setForm({ ...form, ...updates })
  }

  const parseCommaSeparated = (value: string): string[] => {
    return value
      .split(',')
      .map(s => s.trim())
      .filter(s => s.length > 0)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (isEdit) {
      // null removes a field the user cleared; the server merges contact_info.
      const contact_info = Object.fromEntries(CONTACT_FIELDS.map(({ key }) => [key, form[key] || null]))
      const data: PersonUpdate = {
        display_name: form.display_name || undefined,
        aliases: parseCommaSeparated(form.aliases),
        tags: parseCommaSeparated(form.tags),
        content: form.notes || undefined,
        contact_info,
        replace_aliases: true,
      }
      await onSubmit(data)
    } else {
      const contact_info = Object.fromEntries(CONTACT_FIELDS.filter(({ key }) => form[key]).map(({ key }) => [key, form[key]]))
      const data: PersonCreate = {
        identifier: form.identifier,
        display_name: form.display_name,
        aliases: parseCommaSeparated(form.aliases),
        tags: parseCommaSeparated(form.tags),
        content: form.notes || undefined,
        contact_info: Object.keys(contact_info).length > 0 ? contact_info : undefined,
      }
      await onSubmit(data)
    }
  }

  const identifierHint = isEdit ? undefined
    : form.display_name && !form.identifier ? "This name has no letters we can build an identifier from: type an identifier (lowercase letters, numbers, underscores)."
    : 'Auto-generated from the name; lowercase, no spaces. Cannot be changed later.'

  return (
    <Modal title={title} onClose={onCancel}>
      {error && (
          <Notice tone="error" role="alert" className="mb-4">
            {error}
          </Notice>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label={requiredLabel('Display name')}>
            <Input
              id="person-display-name"
              type="text"
              value={form.display_name}
              onChange={(e) => handleDisplayNameChange(e.target.value)}
              required
              placeholder="e.g., Alice Chen"
            />
          </Field>

          <Field label={requiredLabel('Identifier')} hint={identifierHint}>
            <Input
              id="person-identifier"
              type="text"
              value={form.identifier}
              onChange={(e) => setForm({ ...form, identifier: e.target.value })}
              required
              disabled={isEdit}
              placeholder="e.g., alice_chen"
              pattern="[a-z0-9_]+"
              title="Only lowercase letters, numbers, and underscores"
              className="disabled:bg-slate-50"
            />
          </Field>

          <Field label="Aliases" hint="Comma-separated alternative names or handles">
            <Input
              id="person-aliases"
              type="text"
              value={form.aliases}
              onChange={(e) => setForm({ ...form, aliases: e.target.value })}
              placeholder="e.g., @alice_c, alice.chen@work.com"
            />
          </Field>

          <Field label="Tags" hint="Comma-separated tags for categorization">
            <Input
              id="person-tags"
              type="text"
              value={form.tags}
              onChange={(e) => setForm({ ...form, tags: e.target.value })}
              placeholder="e.g., work, friend, engineering"
            />
          </Field>

          <Fieldset legend="Contact information" className="pt-2 space-y-3">
            <FieldRow>
              {CONTACT_FIELDS.map(({ key, label, type, placeholder }) => (
                <Field key={key} label={label}>
                  <Input
                    id={`person-${key}`}
                    type={type}
                    value={form[key]}
                    onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                    placeholder={placeholder}
                  />
                </Field>
              ))}
            </FieldRow>
          </Fieldset>

          <Field label="Add a note">
            <Textarea
              id="person-notes"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              rows={4}
              placeholder="Saved as a tidbit. Existing tidbits are shown on the person card."
            />
          </Field>

          <FormActions>
            <Button type="button" onClick={onCancel}>Cancel</Button>
            <Button variant="primary"
              type="submit"
              disabled={loading || !form.display_name || !form.identifier}
            >
              {loading ? 'Saving...' : submitLabel}
            </Button>
          </FormActions>
        </form>
    </Modal>
  )
}

export default PersonFormModal
