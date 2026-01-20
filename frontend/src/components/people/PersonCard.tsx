import React from 'react'
import type { Person } from '../../hooks/usePeople'

interface PersonCardProps {
  person: Person
  onEdit: (person: Person) => void
  onDelete: (identifier: string) => void
  expanded?: boolean
  onToggleExpand?: () => void
  disabled?: boolean
}

export const PersonCard: React.FC<PersonCardProps> = ({
  person,
  onEdit,
  onDelete,
  expanded = false,
  onToggleExpand,
  disabled = false,
}) => {
  const [deleteConfirm, setDeleteConfirm] = React.useState(false)

  const handleDelete = () => {
    if (disabled) return
    if (deleteConfirm) {
      onDelete(person.identifier)
      setDeleteConfirm(false)
    } else {
      setDeleteConfirm(true)
    }
  }

  const contactEntries = Object.entries(person.contact_info || {})

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden hover:shadow-md transition-shadow">
      {/* Header - always visible */}
      <div
        className="p-5 cursor-pointer"
        onClick={onToggleExpand}
      >
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <h3 className="font-semibold text-slate-800 text-lg">
              {person.display_name}
            </h3>
            <p className="text-sm text-slate-500">@{person.identifier}</p>
          </div>
          <button
            type="button"
            className="text-slate-400 hover:text-slate-600 p-1"
            onClick={(e) => {
              e.stopPropagation()
              onToggleExpand?.()
            }}
          >
            <svg
              className={`w-5 h-5 transition-transform ${expanded ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>

        {/* Quick info - always visible */}
        {person.tags && person.tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-3">
            {person.tags.slice(0, expanded ? undefined : 5).map((tag) => (
              <span
                key={tag}
                className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded text-xs"
              >
                {tag}
              </span>
            ))}
            {!expanded && person.tags.length > 5 && (
              <span className="px-2 py-0.5 text-slate-400 text-xs">
                +{person.tags.length - 5} more
              </span>
            )}
          </div>
        )}
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="border-t border-slate-100 px-5 py-4 space-y-4">
          {/* Aliases */}
          {person.aliases && person.aliases.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Aliases
              </h4>
              <div className="flex flex-wrap gap-2">
                {person.aliases.map((alias) => (
                  <span
                    key={alias}
                    className="px-2 py-1 bg-blue-50 text-blue-700 rounded text-sm"
                  >
                    {alias}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Contact Info */}
          {contactEntries.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Contact
              </h4>
              <dl className="space-y-1">
                {contactEntries.map(([key, value]) => (
                  <div key={key} className="flex gap-2 text-sm">
                    <dt className="text-slate-500 capitalize">{key}:</dt>
                    <dd className="text-slate-800">
                      {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {/* Notes */}
          {person.notes && (
            <div>
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Notes
              </h4>
              <p className="text-sm text-slate-700 whitespace-pre-wrap">
                {person.notes}
              </p>
            </div>
          )}

          {/* Created date */}
          {person.created_at && (
            <p className="text-xs text-slate-400">
              Added {new Date(person.created_at).toLocaleDateString()}
            </p>
          )}

          {/* Actions */}
          <div className="flex gap-2 pt-2 border-t border-slate-100">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                onEdit(person)
              }}
              disabled={disabled}
              className="py-1.5 px-3 bg-slate-100 text-slate-700 rounded text-sm hover:bg-slate-200 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Edit
            </button>
            {deleteConfirm ? (
              <div className="flex items-center gap-2 ml-auto">
                <span className="text-sm text-slate-600">{disabled ? 'Deleting...' : 'Delete?'}</span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    handleDelete()
                  }}
                  disabled={disabled}
                  className="py-1.5 px-3 bg-red-600 text-white rounded text-sm hover:bg-red-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
                >
                  {disabled ? '...' : 'Yes'}
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    setDeleteConfirm(false)
                  }}
                  disabled={disabled}
                  className="py-1.5 px-3 bg-slate-100 text-slate-700 rounded text-sm hover:bg-slate-200 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  No
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  handleDelete()
                }}
                disabled={disabled}
                className="py-1.5 px-3 border border-red-200 text-red-600 rounded text-sm hover:bg-red-50 ml-auto disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Delete
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default PersonCard
