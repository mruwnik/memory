import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { usePeople, type Person, type PersonCreate, type PersonUpdate } from '../../hooks/usePeople'
import PersonCard from './PersonCard'
import PersonFormModal from './PersonFormModal'

// Custom hook for debouncing values
function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])

  return debouncedValue
}

const PeopleManagement = () => {
  const { listPeople, addPerson, updatePerson, deletePerson } = usePeople()

  const [people, setPeople] = useState<Person[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Search/filter state
  const [searchTerm, setSearchTerm] = useState('')
  const [tagFilter, setTagFilter] = useState<string[]>([])

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

  // Expanded card state
  const [expandedIdentifier, setExpandedIdentifier] = useState<string | null>(null)

  // Collect all unique tags from people for the filter
  const allTags = Array.from(new Set(people.flatMap(p => p.tags || []))).sort()

  const loadPeople = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listPeople({
        search: debouncedSearchTerm || undefined,
        tags: tagFilter.length > 0 ? tagFilter : undefined,
        limit: 200,
      })
      setPeople(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load people')
    } finally {
      setLoading(false)
    }
  }, [listPeople, debouncedSearchTerm, tagFilter])

  useEffect(() => {
    loadPeople()
  }, [loadPeople])

  const handleCreate = async (data: PersonCreate) => {
    setCreateLoading(true)
    setCreateError(null)

    try {
      await addPerson(data)
      setShowCreateModal(false)
      // Wait a moment for the background task to complete
      setTimeout(() => loadPeople(), 500)
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
      // Wait a moment for the background task to complete
      setTimeout(() => loadPeople(), 500)
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

  const toggleExpanded = (identifier: string) => {
    setExpandedIdentifier(prev => prev === identifier ? null : identifier)
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
            <button
              onClick={loadPeople}
              disabled={loading}
              className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors disabled:opacity-50"
            >
              {loading ? 'Loading...' : 'Refresh'}
            </button>
          </div>

          {/* Tag filters */}
          {allTags.length > 0 && (
            <div className="flex flex-wrap gap-2">
              <span className="text-sm text-slate-500 self-center mr-2">Filter by tag:</span>
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
              <PersonCard
                key={person.identifier}
                person={person}
                expanded={expandedIdentifier === person.identifier}
                onToggleExpand={() => toggleExpanded(person.identifier)}
                onEdit={() => setEditingPerson(person)}
                onDelete={() => setDeletingPerson(person)}
              />
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
    </div>
  )
}

export default PeopleManagement
