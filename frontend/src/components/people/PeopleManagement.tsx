import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { usePeople, type Person, type PersonCreate, type PersonUpdate, type Tidbit } from '../../hooks/usePeople'
import { useTeams, type Team } from '../../hooks/useTeams'
import { useDebounce } from '../../hooks/useDebounce'
import PersonCard from './PersonCard'
import PersonFormModal from './PersonFormModal'

const PeopleManagement = () => {
  const { listPeople, addPerson, updatePerson, deletePerson, getPerson, mergePeople } = usePeople()
  const { getPersonTeams, listTeams, listMembers } = useTeams()

  const [people, setPeople] = useState<Person[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Teams cache per person identifier
  const [personTeams, setPersonTeams] = useState<Record<string, Team[]>>({})
  const [teamsLoading, setTeamsLoading] = useState<Record<string, boolean>>({})

  // Search/filter state
  const [searchTerm, setSearchTerm] = useState('')
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [teamFilter, setTeamFilter] = useState<number | null>(null)

  // All teams for filter dropdown
  const [allTeams, setAllTeams] = useState<Team[]>([])
  const [showTeamFilter, setShowTeamFilter] = useState(false)

  // Person details with tidbits (keyed by identifier)
  const [personDetails, setPersonDetails] = useState<Record<string, Person>>({})
  const [detailsLoading, setDetailsLoading] = useState<Record<string, boolean>>({})

  // Debounce search term to avoid excessive API calls on every keystroke
  const debouncedSearchTerm = useDebounce(searchTerm, 300)

  // Create modal state
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [createLoading, setCreateLoading] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  // Edit modal state
  const [editingPerson, setEditingPerson] = useState<Person | null>(null)
  const [editLoading, setEditLoading] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  // Delete confirmation
  const [deletingPerson, setDeletingPerson] = useState<Person | null>(null)
  const [deleteLoading, setDeleteLoading] = useState(false)

  // Merge state
  const [selectedForMerge, setSelectedForMerge] = useState<Set<string>>(new Set())
  const [showMergeModal, setShowMergeModal] = useState(false)
  const [mergeLoading, setMergeLoading] = useState(false)
  const [mergePrimaryId, setMergePrimaryId] = useState<string | null>(null)

  // Expanded card state
  const [expandedIdentifier, setExpandedIdentifier] = useState<string | null>(null)

  // Collect all unique tags from people for the filter (memoized to avoid recomputing on every render)
  const allTags = useMemo(
    () => Array.from(new Set(people.flatMap(p => p.tags || []))).sort(),
    [people]
  )

  const loadPeople = useCallback(async () => {
    setLoading(true)
    try {
      let data = await listPeople({
        search: debouncedSearchTerm || undefined,
        tags: tagFilter.length > 0 ? tagFilter : undefined,
        limit: 200,
      })

      // If team filter is active, filter to only people in that team
      if (teamFilter !== null) {
        const teamMembers = await listMembers(teamFilter)
        const memberIdentifiers = new Set(teamMembers.map(m => m.identifier))
        data = data.filter(p => memberIdentifiers.has(p.identifier))
      }

      setPeople(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load people')
    } finally {
      setLoading(false)
    }
  }, [listPeople, listMembers, debouncedSearchTerm, tagFilter, teamFilter])

  useEffect(() => {
    loadPeople()
  }, [loadPeople])

  // Load teams for filter dropdown
  useEffect(() => {
    const loadAllTeams = async () => {
      try {
        const teams = await listTeams({ include_inactive: false })
        setAllTeams(teams)
      } catch {
        // Silently fail - team filter just won't appear
      }
    }
    loadAllTeams()
  }, [listTeams])

  const handleCreate = async (data: PersonCreate) => {
    setCreateLoading(true)
    setCreateError(null)

    try {
      await addPerson(data)
      setShowCreateModal(false)
      // Background task processes asynchronously via Celery.
      // Delay refresh to allow task completion. This is a pragmatic approach;
      // for guaranteed consistency, consider polling task status or using SSE.
      setTimeout(() => loadPeople(), 1000)
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : 'Failed to create person')
    } finally {
      setCreateLoading(false)
    }
  }

  const handleUpdate = async (data: PersonUpdate) => {
    if (!editingPerson) return

    setEditLoading(true)
    setEditError(null)

    try {
      await updatePerson(editingPerson.identifier, data)
      setEditingPerson(null)
      // Background task processes asynchronously via Celery.
      // Delay refresh to allow task completion. This is a pragmatic approach;
      // for guaranteed consistency, consider polling task status or using SSE.
      setTimeout(() => loadPeople(), 1000)
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Failed to update person')
    } finally {
      setEditLoading(false)
    }
  }

  const handleDelete = async () => {
    if (!deletingPerson) return

    setDeleteLoading(true)

    try {
      await deletePerson(deletingPerson.identifier)
      setDeletingPerson(null)
      await loadPeople()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete person')
    } finally {
      setDeleteLoading(false)
    }
  }

  const toggleTagFilter = (tag: string) => {
    setTagFilter(prev =>
      prev.includes(tag)
        ? prev.filter(t => t !== tag)
        : [...prev, tag]
    )
  }

  const toggleMergeSelection = (identifier: string) => {
    setSelectedForMerge(prev => {
      const next = new Set(prev)
      if (next.has(identifier)) {
        next.delete(identifier)
      } else {
        next.add(identifier)
      }
      return next
    })
  }

  const clearMergeSelection = () => {
    setSelectedForMerge(new Set())
    setMergePrimaryId(null)
  }

  const handleMerge = async () => {
    if (selectedForMerge.size < 2) return

    setMergeLoading(true)

    try {
      const identifiers = Array.from(selectedForMerge)
      const result = await mergePeople(identifiers, mergePrimaryId || undefined)

      if (result.success) {
        setShowMergeModal(false)
        clearMergeSelection()
        await loadPeople()
      } else {
        setError(result.error || 'Failed to merge people')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to merge people')
    } finally {
      setMergeLoading(false)
    }
  }

  const toggleExpanded = async (identifier: string) => {
    const isExpanding = expandedIdentifier !== identifier
    setExpandedIdentifier(prev => prev === identifier ? null : identifier)

    if (!isExpanding) return

    // Fetch teams when expanding, if not already loaded
    if (!personTeams[identifier] && !teamsLoading[identifier]) {
      setTeamsLoading(prev => ({ ...prev, [identifier]: true }))
      try {
        const teams = await getPersonTeams(identifier)
        setPersonTeams(prev => ({ ...prev, [identifier]: teams }))
      } catch (e) {
        console.error('Failed to load teams for person:', e)
        setPersonTeams(prev => ({ ...prev, [identifier]: [] }))
      } finally {
        setTeamsLoading(prev => ({ ...prev, [identifier]: false }))
      }
    }

    // Fetch person details with tidbits when expanding, if not already loaded
    if (!personDetails[identifier] && !detailsLoading[identifier]) {
      setDetailsLoading(prev => ({ ...prev, [identifier]: true }))
      try {
        const details = await getPerson(identifier, true)
        if (details) {
          setPersonDetails(prev => ({ ...prev, [identifier]: details }))
        }
      } catch (e) {
        console.error('Failed to load person details:', e)
      } finally {
        setDetailsLoading(prev => ({ ...prev, [identifier]: false }))
      }
    }
  }

  if (loading && people.length === 0) {
    return (
      <div className="min-h-screen bg-slate-50 p-8 flex items-center justify-center">
        <p className="text-slate-500">Loading...</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50 p-8">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <header className="flex items-center gap-4 mb-8 pb-4 border-b border-slate-200">
          <Link
            to="/ui/dashboard"
            className="bg-slate-100 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-200 transition-colors"
          >
            &larr; Back
          </Link>
          <h1 className="text-2xl font-semibold text-slate-800 flex-1">People</h1>

          {/* Merge controls */}
          {selectedForMerge.size > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-slate-600">
                {selectedForMerge.size} selected
              </span>
              <button
                onClick={clearMergeSelection}
                className="text-sm text-slate-500 hover:text-slate-700"
              >
                Clear
              </button>
              {selectedForMerge.size >= 2 && (
                <button
                  onClick={() => {
                    setMergePrimaryId(Array.from(selectedForMerge)[0])
                    setShowMergeModal(true)
                  }}
                  className="bg-purple-600 text-white py-2 px-4 rounded-lg hover:bg-purple-700 transition-colors flex items-center gap-2"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                  </svg>
                  Merge
                </button>
              )}
            </div>
          )}

          <button
            onClick={() => setShowCreateModal(true)}
            className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Add Person
          </button>
        </header>

        {/* Error message */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 text-red-700 rounded-lg">
            {error}
            <button onClick={() => setError(null)} className="ml-4 underline">
              Dismiss
            </button>
          </div>
        )}

        {/* Search and filters */}
        <div className="mb-6 space-y-4">
          <div className="flex gap-4">
            <div className="flex-1">
              <input
                type="text"
                placeholder="Search by name, alias, or notes..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full py-2 px-4 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
            </div>

            {/* Team Filter Dropdown */}
            {allTeams.length > 0 && (
              <div className="relative">
                <button
                  onClick={() => setShowTeamFilter(!showTeamFilter)}
                  className={`px-4 py-2 bg-white border rounded-lg text-sm flex items-center gap-2 hover:bg-slate-50 transition-colors ${
                    teamFilter !== null ? 'border-primary text-primary' : 'border-slate-200 text-slate-700'
                  }`}
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                  </svg>
                  <span>
                    {teamFilter !== null
                      ? allTeams.find(t => t.id === teamFilter)?.name || 'Team'
                      : 'All Teams'}
                  </span>
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                {showTeamFilter && (
                  <>
                    <div
                      className="fixed inset-0 z-40"
                      onClick={() => setShowTeamFilter(false)}
                    />
                    <div className="absolute right-0 top-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-64 max-h-80 overflow-auto">
                      <button
                        onClick={() => {
                          setTeamFilter(null)
                          setShowTeamFilter(false)
                        }}
                        className={`w-full text-left px-4 py-2 text-sm hover:bg-slate-50 ${
                          teamFilter === null ? 'bg-primary/10 text-primary' : 'text-slate-700'
                        }`}
                      >
                        All Teams
                      </button>
                      <div className="border-t border-slate-100">
                        {allTeams.map(team => (
                          <button
                            key={team.id}
                            onClick={() => {
                              setTeamFilter(team.id)
                              setShowTeamFilter(false)
                            }}
                            className={`w-full text-left px-4 py-2 text-sm hover:bg-slate-50 flex items-center justify-between ${
                              teamFilter === team.id ? 'bg-primary/10 text-primary' : 'text-slate-700'
                            }`}
                          >
                            <span>{team.name}</span>
                            {team.member_count !== undefined && (
                              <span className="text-xs text-slate-400">{team.member_count}</span>
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}

            <button
              onClick={loadPeople}
              disabled={loading}
              className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors disabled:opacity-50"
            >
              {loading ? 'Loading...' : 'Refresh'}
            </button>
          </div>

          {/* Tag filters - collapsible */}
          {allTags.length > 0 && (
            <details className="group">
              <summary className="text-sm text-slate-500 cursor-pointer hover:text-slate-700 select-none list-none flex items-center gap-2">
                <svg
                  className="w-4 h-4 transition-transform group-open:rotate-90"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                <span className="flex items-center gap-2">
                  Filter by tag
                  {tagFilter.length > 0 && (
                    <span className="bg-primary text-white text-xs px-2 py-0.5 rounded-full">
                      {tagFilter.length} selected
                    </span>
                  )}
                </span>
              </summary>
              <div className="flex flex-wrap gap-2 mt-3 pl-6">
                {allTags.map(tag => (
                  <button
                    key={tag}
                    onClick={() => toggleTagFilter(tag)}
                    className={`px-3 py-1 rounded-full text-sm transition-colors ${
                      tagFilter.includes(tag)
                        ? 'bg-primary text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    {tag}
                  </button>
                ))}
                {tagFilter.length > 0 && (
                  <button
                    onClick={() => setTagFilter([])}
                    className="px-3 py-1 rounded-full text-sm text-slate-500 hover:text-slate-700 underline"
                  >
                    Clear
                  </button>
                )}
              </div>
            </details>
          )}
        </div>

        {/* People list */}
        <div className="space-y-4">
          {people.length === 0 ? (
            <div className="bg-white rounded-xl shadow-md p-8 text-center text-slate-500">
              {searchTerm || tagFilter.length > 0
                ? 'No people found matching your search.'
                : 'No people tracked yet. Add your first person above.'}
            </div>
          ) : (
            people.map((person) => (
              <div key={person.identifier} className="flex items-start gap-3">
                {/* Selection checkbox for merge */}
                <div className="pt-6">
                  <input
                    type="checkbox"
                    checked={selectedForMerge.has(person.identifier)}
                    onChange={() => toggleMergeSelection(person.identifier)}
                    className="w-4 h-4 rounded border-slate-300 text-purple-600 focus:ring-purple-500 cursor-pointer"
                    title="Select for merge"
                  />
                </div>
                <div className="flex-1">
                  <PersonCard
                    person={person}
                    expanded={expandedIdentifier === person.identifier}
                    onToggleExpand={() => toggleExpanded(person.identifier)}
                    onEdit={() => setEditingPerson(person)}
                    onDelete={() => setDeletingPerson(person)}
                    teams={personTeams[person.identifier]}
                    teamsLoading={teamsLoading[person.identifier]}
                    tidbits={personDetails[person.identifier]?.tidbits}
                    tidbitsLoading={detailsLoading[person.identifier]}
                  />
                </div>
              </div>
            ))
          )}
        </div>

        {/* Summary */}
        {people.length > 0 && (
          <div className="mt-6 text-sm text-slate-500 text-center">
            Showing {people.length} {people.length === 1 ? 'person' : 'people'}
          </div>
        )}
      </div>

      {/* Create Person Modal */}
      {showCreateModal && (
        <PersonFormModal
          title="Add New Person"
          onSubmit={handleCreate}
          onCancel={() => {
            setShowCreateModal(false)
            setCreateError(null)
          }}
          loading={createLoading}
          error={createError}
          submitLabel="Add Person"
        />
      )}

      {/* Edit Person Modal */}
      {editingPerson && (
        <PersonFormModal
          title="Edit Person"
          initialData={editingPerson}
          onSubmit={handleUpdate}
          onCancel={() => {
            setEditingPerson(null)
            setEditError(null)
          }}
          loading={editLoading}
          error={editError}
          submitLabel="Save Changes"
          isEdit
        />
      )}

      {/* Delete Confirmation Modal */}
      {deletingPerson && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md m-4">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">Delete Person</h3>

            <p className="text-sm text-slate-600 mb-4">
              Are you sure you want to delete <strong>{deletingPerson.display_name}</strong>? This action cannot
              be undone.
            </p>

            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeletingPerson(null)}
                className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={deleteLoading}
                className="bg-red-600 text-white py-2 px-4 rounded-lg hover:bg-red-700 disabled:bg-slate-400 transition-colors"
              >
                {deleteLoading ? 'Deleting...' : 'Delete Person'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Merge Modal */}
      {showMergeModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-lg m-4">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">Merge People</h3>

            <p className="text-sm text-slate-600 mb-4">
              Merge {selectedForMerge.size} people into one. All tidbits, team memberships, and other
              associations will be moved to the primary person. The other records will be deleted.
            </p>

            <div className="mb-4">
              <label className="block text-sm font-medium text-slate-700 mb-2">
                Select primary person (to keep):
              </label>
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {Array.from(selectedForMerge).map(identifier => {
                  const person = people.find(p => p.identifier === identifier)
                  return (
                    <label
                      key={identifier}
                      className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                        mergePrimaryId === identifier
                          ? 'bg-purple-50 border border-purple-200'
                          : 'bg-slate-50 hover:bg-slate-100'
                      }`}
                    >
                      <input
                        type="radio"
                        name="primary"
                        checked={mergePrimaryId === identifier}
                        onChange={() => setMergePrimaryId(identifier)}
                        className="text-purple-600 focus:ring-purple-500"
                      />
                      <div>
                        <div className="font-medium text-slate-800">
                          {person?.display_name || identifier}
                        </div>
                        <div className="text-xs text-slate-500">@{identifier}</div>
                      </div>
                    </label>
                  )
                })}
              </div>
            </div>

            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
              <div className="flex items-start gap-2">
                <svg className="w-5 h-5 text-amber-600 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <div className="text-sm text-amber-800">
                  <strong>This action cannot be undone.</strong> The secondary person records will be permanently deleted.
                  Their identifiers and names will be preserved as aliases on the primary person.
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowMergeModal(false)}
                className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleMerge}
                disabled={mergeLoading || !mergePrimaryId}
                className="bg-purple-600 text-white py-2 px-4 rounded-lg hover:bg-purple-700 disabled:bg-slate-400 transition-colors"
              >
                {mergeLoading ? 'Merging...' : 'Merge People'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default PeopleManagement
