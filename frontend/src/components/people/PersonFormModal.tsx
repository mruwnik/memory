import { useState, useRef, useEffect } from 'react'
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

// Selectors for all normally focusable elements inside a container.
const FOCUSABLE_SELECTORS =
  'a[href], area[href], input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'

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

  const dialogRef = useRef<HTMLDivElement>(null)

  // Move focus to first focusable element on mount; trap Tab within the dialog.
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return

    const getFocusable = () =>
      Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTORS))

    // Focus first field immediately.
    getFocusable()[0]?.focus()

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancel()
        return
      }
      if (e.key !== 'Tab') return

      const items = getFocusable()
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      const focused = document.activeElement as HTMLElement

      if (e.shiftKey) {
        if (focused === first) {
          e.preventDefault()
          last.focus()
        }
      } else {
        if (focused === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }

    dialog.addEventListener('keydown', handleKeyDown)
    return () => dialog.removeEventListener('keydown', handleKeyDown)
  }, [onCancel])

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
        notes: form.notes || undefined,
        contact_info: Object.keys(contact_info).length > 0 ? contact_info : undefined,
        // Use replace mode for tags and aliases so removals work
        replace_tags: true,
        replace_aliases: true,
        replace_notes: true,
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
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="person-modal-title"
        className="bg-white rounded-xl shadow-xl p-6 w-full max-w-lg m-4 max-h-[90vh] overflow-y-auto"
      >
        <h3 id="person-modal-title" className="text-lg font-semibold text-slate-800 mb-4">
          {title}
        </h3>

        {error && (
          <div role="alert" className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Basic Info */}
          <div>
            <label htmlFor="person-display-name" className="block text-sm font-medium text-slate-700 mb-1">
              Display Name <span className="text-red-500" aria-hidden="true">*</span>
              <span className="sr-only">(required)</span>
            </label>
            <input
              id="person-display-name"
              type="text"
              value={form.display_name}
              onChange={(e) => handleDisplayNameChange(e.target.value)}
              required
              placeholder="e.g., Alice Chen"
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </div>

          <div>
            <label htmlFor="person-identifier" className="block text-sm font-medium text-slate-700 mb-1">
              Identifier <span className="text-red-500" aria-hidden="true">*</span>
              <span className="sr-only">(required)</span>
              {!isEdit && <span className="font-normal text-slate-400 ml-1">(auto-generated)</span>}
            </label>
            <input
              id="person-identifier"
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
            <label htmlFor="person-aliases" className="block text-sm font-medium text-slate-700 mb-1">
              Aliases
            </label>
            <input
              id="person-aliases"
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
            <label htmlFor="person-tags" className="block text-sm font-medium text-slate-700 mb-1">
              Tags
            </label>
            <input
              id="person-tags"
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
                <label htmlFor="person-email" className="block text-xs text-slate-500 mb-1">Email</label>
                <input
                  id="person-email"
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="alice@example.com"
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div>
                <label htmlFor="person-phone" className="block text-xs text-slate-500 mb-1">Phone</label>
                <input
                  id="person-phone"
                  type="tel"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  placeholder="+1 555-1234"
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div>
                <label htmlFor="person-github" className="block text-xs text-slate-500 mb-1">GitHub</label>
                <input
                  id="person-github"
                  type="text"
                  value={form.github}
                  onChange={(e) => setForm({ ...form, github: e.target.value })}
                  placeholder="@alicechen"
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div>
                <label htmlFor="person-twitter" className="block text-xs text-slate-500 mb-1">Twitter/X</label>
                <input
                  id="person-twitter"
                  type="text"
                  value={form.twitter}
                  onChange={(e) => setForm({ ...form, twitter: e.target.value })}
                  placeholder="@alice_c"
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <div className="col-span-2">
                <label htmlFor="person-website" className="block text-xs text-slate-500 mb-1">Website</label>
                <input
                  id="person-website"
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
            <label htmlFor="person-notes" className="block text-sm font-medium text-slate-700 mb-1">
              Notes
            </label>
            <textarea
              id="person-notes"
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
