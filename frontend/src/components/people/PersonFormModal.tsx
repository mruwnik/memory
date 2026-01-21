import { useState } from 'react'
import type { Person, PersonCreate, PersonUpdate } from '../../hooks/usePeople'

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
    notes: initialData?.notes || '',
    email: initialData?.contact_info?.email || '',
    phone: initialData?.contact_info?.phone || '',
    github: initialData?.contact_info?.github || '',
    twitter: initialData?.contact_info?.twitter || '',
    website: initialData?.contact_info?.website || '',
  })

  const generateIdentifier = (name: string): string => {
    return name
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
        aliases: form.aliases ? parseCommaSeparated(form.aliases) : undefined,
        tags: form.tags ? parseCommaSeparated(form.tags) : undefined,
        notes: form.notes || undefined,
        contact_info: Object.keys(contact_info).length > 0 ? contact_info : undefined,
      }
      await onSubmit(data)
    } else {
      const data: PersonCreate = {
        identifier: form.identifier,
        display_name: form.display_name,
        aliases: parseCommaSeparated(form.aliases),
        tags: parseCommaSeparated(form.tags),
        notes: form.notes || undefined,
        contact_info: Object.keys(contact_info).length > 0 ? contact_info : undefined,
      }
      await onSubmit(data)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-lg m-4 max-h-[90vh] overflow-y-auto">
        <h3 className={`text-lg font-semibold text-slate-800 ${isEdit ? 'mb-2' : 'mb-4'}`}>{title}</h3>
        {isEdit && (
          <p className="text-xs text-slate-500 mb-4">
            Note: Aliases and tags are merged with existing values (added, not replaced).
          </p>
        )}

        {error && (
          <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg text-sm">{error}</div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Basic Info */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Display Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={form.display_name}
              onChange={(e) => handleDisplayNameChange(e.target.value)}
              required
              placeholder="e.g., Alice Chen"
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Identifier <span className="text-red-500">*</span>
              {!isEdit && <span className="font-normal text-slate-400 ml-1">(auto-generated)</span>}
            </label>
            <input
              type="text"
              value={form.identifier}
              onChange={(e) => setForm({ ...form, identifier: e.target.value })}
              required
              disabled={isEdit}
              placeholder="e.g., alice_chen"
              pattern="[a-z0-9_]+"
              title="Only lowercase letters, numbers, and underscores"
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:bg-slate-50 disabled:text-slate-500"
            />
            {!isEdit && (
              <p className="text-xs text-slate-400 mt-1">
                Unique identifier (lowercase, no spaces). Cannot be changed later.
              </p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Aliases
            </label>
            <input
              type="text"
              value={form.aliases}
              onChange={(e) => setForm({ ...form, aliases: e.target.value })}
              placeholder="e.g., @alice_c, alice.chen@work.com"
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            <p className="text-xs text-slate-400 mt-1">
              Comma-separated alternative names or handles
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Tags
            </label>
            <input
              type="text"
              value={form.tags}
              onChange={(e) => setForm({ ...form, tags: e.target.value })}
              placeholder="e.g., work, friend, engineering"
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            <p className="text-xs text-slate-400 mt-1">
              Comma-separated tags for categorization
            </p>
          </div>

          {/* Contact Info Section */}
          <div className="pt-2">
            <h4 className="text-sm font-medium text-slate-700 mb-3">Contact Information</h4>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-slate-500 mb-1">Email</label>
                <input
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="alice@example.com"
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Phone</label>
                <input
                  type="tel"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  placeholder="+1 555-1234"
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">GitHub</label>
                <input
                  type="text"
                  value={form.github}
                  onChange={(e) => setForm({ ...form, github: e.target.value })}
                  placeholder="@alicechen"
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Twitter/X</label>
                <input
                  type="text"
                  value={form.twitter}
                  onChange={(e) => setForm({ ...form, twitter: e.target.value })}
                  placeholder="@alice_c"
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs text-slate-500 mb-1">Website</label>
                <input
                  type="url"
                  value={form.website}
                  onChange={(e) => setForm({ ...form, website: e.target.value })}
                  placeholder="https://alice.dev"
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Notes
            </label>
            <textarea
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              rows={4}
              placeholder="Free-form notes about this person..."
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 resize-none"
            />
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4">
            <button
              type="button"
              onClick={onCancel}
              className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !form.display_name || !form.identifier}
              className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 disabled:bg-slate-400 transition-colors"
            >
              {loading ? 'Saving...' : submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default PersonFormModal
