import React, { useState, useEffect } from 'react'
import { usePeople } from '../../hooks/usePeople'
import type { Person } from '../../hooks/usePeople'

interface PersonModalProps {
  person: Person | null
  onClose: () => void
  onSuccess: () => void
}

interface ContactEntry {
  key: string
  value: string
}

export const PersonModal: React.FC<PersonModalProps> = ({
  person,
  onClose,
  onSuccess,
}) => {
  const { createPerson, updatePerson } = usePeople()
  const isEditing = person !== null

  const [identifier, setIdentifier] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [aliasesText, setAliasesText] = useState('')
  const [contactEntries, setContactEntries] = useState<ContactEntry[]>([])
  const [tagsText, setTagsText] = useState('')
  const [notes, setNotes] = useState('')
  const [replaceNotes, setReplaceNotes] = useState(false)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [identifierManuallyEdited, setIdentifierManuallyEdited] = useState(false)

  useEffect(() => {
    if (person) {
      setIdentifier(person.identifier)
      setDisplayName(person.display_name)
      setAliasesText(person.aliases?.join(', ') || '')
      setContactEntries(
        Object.entries(person.contact_info || {}).map(([key, value]) => ({
          key,
          value: typeof value === 'object' ? JSON.stringify(value) : String(value),
        }))
      )
      setTagsText(person.tags?.join(', ') || '')
      setNotes(person.notes || '')
    } else {
      setIdentifier('')
      setDisplayName('')
      setAliasesText('')
      setContactEntries([])
      setTagsText('')
      setNotes('')
      setIdentifierManuallyEdited(false)
    }
  }, [person])

  const handleAddContact = () => {
    setContactEntries([...contactEntries, { key: '', value: '' }])
  }

  const handleRemoveContact = (index: number) => {
    setContactEntries(contactEntries.filter((_, i) => i !== index))
  }

  const handleContactChange = (index: number, field: 'key' | 'value', value: string) => {
    const updated = [...contactEntries]
    updated[index][field] = value
    setContactEntries(updated)
  }

  const generateIdentifier = (name: string): string => {
    return name
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9\s-]/g, '')
      .replace(/\s+/g, '_')
      .replace(/-+/g, '_')
      .substring(0, 50)
  }

  const handleDisplayNameChange = (value: string) => {
    setDisplayName(value)
    // Auto-generate identifier only when creating and user hasn't manually edited it
    if (!isEditing && !identifierManuallyEdited) {
      setIdentifier(generateIdentifier(value))
    }
  }

  const handleIdentifierChange = (value: string) => {
    const sanitized = value.toLowerCase().replace(/[^a-z0-9_-]/g, '')
    setIdentifier(sanitized)
    setIdentifierManuallyEdited(true)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)

    try {
      const aliases = aliasesText
        .split(',')
        .map((a) => a.trim())
        .filter(Boolean)

      const tags = tagsText
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean)

      const contactInfo: Record<string, any> = {}
      for (const entry of contactEntries) {
        if (entry.key.trim()) {
          try {
            contactInfo[entry.key.trim()] = JSON.parse(entry.value)
          } catch {
            contactInfo[entry.key.trim()] = entry.value
          }
        }
      }

      if (isEditing) {
        await updatePerson({
          identifier,
          display_name: displayName || undefined,
          aliases: aliases.length > 0 ? aliases : undefined,
          contact_info: Object.keys(contactInfo).length > 0 ? contactInfo : undefined,
          tags: tags.length > 0 ? tags : undefined,
          notes: notes || undefined,
          replace_notes: replaceNotes,
        })
      } else {
        if (!identifier.trim()) {
          throw new Error('Identifier is required')
        }
        if (!displayName.trim()) {
          throw new Error('Display name is required')
        }
        await createPerson({
          identifier: identifier.trim(),
          display_name: displayName.trim(),
          aliases: aliases.length > 0 ? aliases : undefined,
          contact_info: Object.keys(contactInfo).length > 0 ? contactInfo : undefined,
          tags: tags.length > 0 ? tags : undefined,
          notes: notes || undefined,
        })
      }

      onSuccess()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="p-6 border-b border-slate-200">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-slate-800">
              {isEditing ? 'Edit Person' : 'Add Person'}
            </h2>
            <button
              type="button"
              onClick={onClose}
              className="text-slate-400 hover:text-slate-600"
            >
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-6">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded-lg text-sm">
              {error}
            </div>
          )}

          {/* Basic Info */}
          <div className="space-y-4">
            <div>
              <label htmlFor="displayName" className="block text-sm font-medium text-slate-700 mb-1">
                Display Name <span className="text-red-500">*</span>
              </label>
              <input
                id="displayName"
                type="text"
                value={displayName}
                onChange={(e) => handleDisplayNameChange(e.target.value)}
                className="w-full py-2 px-3 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
                placeholder="Alice Chen"
                required
              />
            </div>

            <div>
              <label htmlFor="identifier" className="block text-sm font-medium text-slate-700 mb-1">
                Identifier <span className="text-red-500">*</span>
                <span className="font-normal text-slate-500 ml-1">(unique, lowercase)</span>
              </label>
              <input
                id="identifier"
                type="text"
                value={identifier}
                onChange={(e) => handleIdentifierChange(e.target.value)}
                className="w-full py-2 px-3 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary font-mono"
                placeholder="alice_chen"
                disabled={isEditing}
                required
              />
              {isEditing && (
                <p className="text-xs text-slate-500 mt-1">Identifier cannot be changed after creation</p>
              )}
            </div>
          </div>

          {/* Aliases */}
          <div>
            <label htmlFor="aliases" className="block text-sm font-medium text-slate-700 mb-1">
              Aliases
              <span className="font-normal text-slate-500 ml-1">(comma separated)</span>
            </label>
            <input
              id="aliases"
              type="text"
              value={aliasesText}
              onChange={(e) => setAliasesText(e.target.value)}
              className="w-full py-2 px-3 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
              placeholder="@alice_c, alice.chen@work.com, Alice"
            />
            {isEditing && (
              <p className="text-xs text-slate-500 mt-1">New aliases will be merged with existing ones</p>
            )}
          </div>

          {/* Contact Info */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-medium text-slate-700">
                Contact Information
              </label>
              <button
                type="button"
                onClick={handleAddContact}
                className="text-sm text-primary hover:underline"
              >
                + Add field
              </button>
            </div>
            {contactEntries.length === 0 ? (
              <p className="text-sm text-slate-500 py-2">No contact info. Click "Add field" to add.</p>
            ) : (
              <div className="space-y-2">
                {contactEntries.map((entry, index) => (
                  <div key={index} className="flex gap-2">
                    <input
                      type="text"
                      value={entry.key}
                      onChange={(e) => handleContactChange(index, 'key', e.target.value)}
                      className="w-32 py-2 px-3 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary text-sm"
                      placeholder="email"
                    />
                    <input
                      type="text"
                      value={entry.value}
                      onChange={(e) => handleContactChange(index, 'value', e.target.value)}
                      className="flex-1 py-2 px-3 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary text-sm"
                      placeholder="alice@example.com"
                    />
                    <button
                      type="button"
                      onClick={() => handleRemoveContact(index)}
                      className="p-2 text-slate-400 hover:text-red-500"
                    >
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            )}
            {isEditing && contactEntries.length > 0 && (
              <p className="text-xs text-slate-500 mt-1">New contact info will be merged with existing</p>
            )}
          </div>

          {/* Tags */}
          <div>
            <label htmlFor="tags" className="block text-sm font-medium text-slate-700 mb-1">
              Tags
              <span className="font-normal text-slate-500 ml-1">(comma separated)</span>
            </label>
            <input
              id="tags"
              type="text"
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              className="w-full py-2 px-3 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
              placeholder="work, engineering, friend"
            />
            {isEditing && (
              <p className="text-xs text-slate-500 mt-1">New tags will be merged with existing ones</p>
            )}
          </div>

          {/* Notes */}
          <div>
            <label htmlFor="notes" className="block text-sm font-medium text-slate-700 mb-1">
              Notes
            </label>
            <textarea
              id="notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={4}
              className="w-full py-2 px-3 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary resize-y"
              placeholder="Any additional notes about this person..."
            />
            {isEditing && (
              <div className="mt-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={replaceNotes}
                    onChange={(e) => setReplaceNotes(e.target.checked)}
                    className="rounded border-slate-300 text-primary focus:ring-primary"
                  />
                  <span className="text-slate-600">Replace existing notes (instead of appending)</span>
                </label>
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4 border-t border-slate-200">
            <button
              type="button"
              onClick={onClose}
              className="py-2 px-4 border border-slate-200 text-slate-700 rounded-lg hover:bg-slate-50"
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="py-2 px-4 bg-primary text-white rounded-lg hover:bg-primary-dark disabled:bg-slate-300"
            >
              {loading ? 'Saving...' : isEditing ? 'Update Person' : 'Add Person'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default PersonModal
