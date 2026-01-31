import type { Person } from '../../hooks/usePeople'

interface PersonCardProps {
  person: Person
  expanded: boolean
  onToggleExpand: () => void
  onEdit: () => void
  onDelete: () => void
}

const PersonCard = ({ person, expanded, onToggleExpand, onEdit, onDelete }: PersonCardProps) => {
  const hasDetails = (person.aliases && person.aliases.length > 0) ||
    (person.contact_info && Object.keys(person.contact_info).length > 0) ||
    person.notes

  const handleCardClick = (e: React.MouseEvent) => {
    // Don't toggle if clicking on buttons or links
    const target = e.target as HTMLElement
    if (target.closest('button') || target.closest('a')) {
      return
    }
    if (hasDetails) {
      onToggleExpand()
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-md overflow-hidden">
      {/* Main content - clickable to expand if has details */}
      <div
        className={`p-6 ${hasDetails ? 'cursor-pointer hover:bg-slate-50/50 transition-colors' : ''}`}
        onClick={handleCardClick}
      >
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-3">
              {/* Avatar placeholder */}
              <div className="w-12 h-12 bg-primary/10 rounded-full flex items-center justify-center">
                <span className="text-primary text-lg font-semibold">
                  {person.display_name.charAt(0).toUpperCase()}
                </span>
              </div>

              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold text-slate-800 text-lg">{person.display_name}</h3>
                  {hasDetails && (
                    <svg
                      className={`w-4 h-4 text-slate-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  )}
                </div>
                <p className="text-sm text-slate-500">@{person.identifier}</p>
              </div>
            </div>

            {/* Tags */}
            {person.tags && person.tags.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-3">
                {person.tags.map((tag) => (
                  <span
                    key={tag}
                    className="bg-slate-100 text-slate-600 px-2 py-0.5 rounded text-xs"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={onEdit}
              className="text-sm text-primary hover:text-primary/80 px-3 py-1 rounded hover:bg-primary/10 transition-colors"
            >
              Edit
            </button>
            <button
              onClick={onDelete}
              className="text-sm text-red-600 hover:text-red-700 px-3 py-1 rounded hover:bg-red-50 transition-colors"
            >
              Delete
            </button>
          </div>
        </div>
      </div>

      {/* Expanded details */}
      {expanded && hasDetails && (
        <div className="border-t border-slate-100 p-6 pt-4 bg-slate-50/50">
          {/* Aliases */}
          {person.aliases && person.aliases.length > 0 && (
            <div className="mb-4">
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Also known as
              </h4>
              <div className="flex flex-wrap gap-2">
                {person.aliases.map((alias, idx) => (
                  <span
                    key={idx}
                    className="bg-white border border-slate-200 text-slate-700 px-2 py-1 rounded text-sm"
                  >
                    {alias}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Contact info */}
          {person.contact_info && Object.keys(person.contact_info).length > 0 && (
            <div className="mb-4">
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Contact Information
              </h4>
              <dl className="grid gap-2">
                {Object.entries(person.contact_info).map(([key, value]) => {
                  // Skip complex nested objects (e.g., slack workspace data)
                  if (typeof value !== 'string') return null
                  return (
                    <div key={key} className="flex">
                      <dt className="text-sm text-slate-500 w-24 flex-shrink-0 capitalize">{key}:</dt>
                      <dd className="text-sm text-slate-700">
                        {key === 'email' ? (
                          <a href={`mailto:${value}`} className="text-primary hover:underline">
                            {value}
                          </a>
                        ) : key === 'phone' ? (
                          <a href={`tel:${value}`} className="text-primary hover:underline">
                            {value}
                          </a>
                        ) : (
                          value
                        )}
                      </dd>
                    </div>
                  )
                })}
              </dl>
            </div>
          )}

          {/* Notes */}
          {person.notes && (
            <div>
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Notes
              </h4>
              <div className="text-sm text-slate-700 whitespace-pre-wrap bg-white border border-slate-200 rounded-lg p-3">
                {person.notes}
              </div>
            </div>
          )}

          {/* Created date */}
          {person.created_at && (
            <div className="mt-4 pt-3 border-t border-slate-200">
              <p className="text-xs text-slate-400">
                Added {new Date(person.created_at).toLocaleDateString()}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default PersonCard
