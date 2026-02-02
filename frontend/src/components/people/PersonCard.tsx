import type { Person, Tidbit } from '../../hooks/usePeople'
import type { Team } from '../../hooks/useTeams'

interface PersonCardProps {
  person: Person
  expanded: boolean
  onToggleExpand: () => void
  onEdit: () => void
  onDelete: () => void
  teams?: Team[]
  teamsLoading?: boolean
  tidbits?: Tidbit[]
  tidbitsLoading?: boolean
}

const PersonCard = ({ person, expanded, onToggleExpand, onEdit, onDelete, teams, teamsLoading, tidbits, tidbitsLoading }: PersonCardProps) => {
  const hasDetails = (person.aliases && person.aliases.length > 0) ||
    (person.contact_info && Object.keys(person.contact_info).length > 0) ||
    person.notes ||
    (teams && teams.length > 0) ||
    (tidbits && tidbits.length > 0)

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
            <div className="mb-4">
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Notes
              </h4>
              <div className="text-sm text-slate-700 whitespace-pre-wrap bg-white border border-slate-200 rounded-lg p-3">
                {person.notes}
              </div>
            </div>
          )}

          {/* Teams */}
          {teamsLoading ? (
            <div className="mb-4">
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Teams
              </h4>
              <p className="text-sm text-slate-400">Loading teams...</p>
            </div>
          ) : teams && teams.length > 0 && (
            <div className="mb-4">
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Teams
              </h4>
              <div className="flex flex-wrap gap-2">
                {teams.map((team) => (
                  <span
                    key={team.id}
                    className="bg-purple-100 text-purple-700 px-3 py-1 rounded-full text-sm flex items-center gap-1"
                  >
                    {team.name}
                    {team.discord_role_id && (
                      <svg className="w-3 h-3 opacity-50" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03z"/>
                      </svg>
                    )}
                    {team.github_team_id && (
                      <svg className="w-3 h-3 opacity-50" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/>
                      </svg>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Tidbits */}
          {tidbitsLoading ? (
            <div className="mb-4">
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Tidbits
              </h4>
              <p className="text-sm text-slate-400">Loading tidbits...</p>
            </div>
          ) : tidbits && tidbits.length > 0 && (
            <div className="mb-4">
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">
                Tidbits ({tidbits.length})
              </h4>
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {tidbits.map((tidbit) => (
                  <div
                    key={tidbit.id}
                    className="bg-white border border-slate-200 rounded-lg p-3"
                  >
                    <div className="text-sm text-slate-700 whitespace-pre-wrap">{tidbit.content}</div>
                    <div className="flex items-center gap-2 mt-2 text-xs text-slate-400">
                      {tidbit.tidbit_type && (
                        <span className="bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
                          {tidbit.tidbit_type}
                        </span>
                      )}
                      {tidbit.source && <span>{tidbit.source}</span>}
                      {tidbit.sensitivity && tidbit.sensitivity !== 'basic' && (
                        <span className={`px-1.5 py-0.5 rounded ${
                          tidbit.sensitivity === 'confidential'
                            ? 'bg-red-100 text-red-600'
                            : 'bg-yellow-100 text-yellow-600'
                        }`}>
                          {tidbit.sensitivity}
                        </span>
                      )}
                      {tidbit.inserted_at && (
                        <span>{new Date(tidbit.inserted_at).toLocaleDateString()}</span>
                      )}
                    </div>
                    {tidbit.tags && tidbit.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {tidbit.tags.map((tag, i) => (
                          <span key={i} className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
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
