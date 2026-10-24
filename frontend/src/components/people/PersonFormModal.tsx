import { useState } from 'react'
import type { Person, PersonCreate, PersonUpdate } from '../../hooks/usePeople'
import { Button, Input, Label, Modal, Notice, Textarea, Field } from '@/components/shared/ui'

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
    // Normalize unicode characters and convert to ASCII-friendly slug
    // Note: This strips non-ASCII chars; international names may need manual adjustment
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

    const contact_info: Record<string, string> = {}
    if (form.email) contact_info.email = form.email
    if (form.phone) contact_info.phone = form.phone
    if (form.github) contact_info.github = form.github
    if (form.twitter) contact_info.twitter = form.twitter
    if (form.website) contact_info.website = form.website

    if (isEdit) {
      const data: PersonUpdate = {
        display_name: form.display_name || undefined,
        aliases: parseCommaSeparated(form.aliases),
        tags: parseCommaSeparated(form.tags),
        content: form.notes || undefined,
        contact_info: Object.keys(contact_info).length > 0 ? contact_info : undefined,
        replace_aliases: true,
      }
      await onSubmit(data)
    } else {
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

  return (
    <Modal title={title} onClose={onCancel}>
      {error && (
          <Notice tone="error" role="alert" className="mb-4">
            {error}
          </Notice>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Basic Info */}
          <Field label={<>Display Name <span className="text-red-500" aria-hidden="true">*</span> <span className="sr-only">(required)</span></>}>
            <Input
              id="person-display-name"
              type="text"
              value={form.display_name}
              onChange={(e) => handleDisplayNameChange(e.target.value)}
              required
              placeholder="e.g., Alice Chen"
            />
          </Field>

          <div>
            <Label htmlFor="person-identifier" className="mb-1">
              Identifier <span className="text-red-500" aria-hidden="true">*</span>
              <span className="sr-only">(required)</span>
              {!isEdit && <span className="font-normal text-slate-500 ml-1">(auto-generated)</span>}
            </Label>
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
            {!isEdit && (
              <p className="text-xs text-slate-500 mt-1">
                Unique identifier (lowercase, no spaces). Cannot be changed later.
              </p>
            )}
          </div>

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

          {/* Contact Info Section */}
          <div className="pt-2">
            <h4 className="text-sm font-medium text-slate-700 mb-3">Contact Information</h4>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="person-email" className="block text-xs text-slate-500 mb-1">Email</label>
                <Input
                  id="person-email"
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="alice@example.com"
                />
              </div>
              <div>
                <label htmlFor="person-phone" className="block text-xs text-slate-500 mb-1">Phone</label>
                <Input
                  id="person-phone"
                  type="tel"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  placeholder="+1 555-1234"
                />
              </div>
              <div>
                <label htmlFor="person-github" className="block text-xs text-slate-500 mb-1">GitHub</label>
                <Input
                  id="person-github"
                  type="text"
                  value={form.github}
                  onChange={(e) => setForm({ ...form, github: e.target.value })}
                  placeholder="@alicechen"
                />
              </div>
              <div>
                <label htmlFor="person-twitter" className="block text-xs text-slate-500 mb-1">Twitter/X</label>
                <Input
                  id="person-twitter"
                  type="text"
                  value={form.twitter}
                  onChange={(e) => setForm({ ...form, twitter: e.target.value })}
                  placeholder="@alice_c"
                />
              </div>
              <div className="col-span-2">
                <label htmlFor="person-website" className="block text-xs text-slate-500 mb-1">Website</label>
                <Input
                  id="person-website"
                  type="url"
                  value={form.website}
                  onChange={(e) => setForm({ ...form, website: e.target.value })}
                  placeholder="https://alice.dev"
                />
              </div>
            </div>
          </div>

          {/* Add a tidbit */}
          <Field label="Add a note">
            <Textarea
              id="person-notes"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              rows={4}
              placeholder="Saved as a tidbit. Existing tidbits are shown on the person card."
            />
          </Field>

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4">
            <Button
              type="button"
              onClick={onCancel}
            >
              Cancel
            </Button>
            <Button variant="primary"
              type="submit"
              disabled={loading || !form.display_name || !form.identifier}
            >
              {loading ? 'Saving...' : submitLabel}
            </Button>
          </div>
        </form>
    </Modal>
  )
}

export default PersonFormModal
