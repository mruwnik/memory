import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { usePeople } from '../../hooks/usePeople'
import type { Person } from '../../hooks/usePeople'
import { PersonCard } from './PersonCard'
import { PersonModal } from './PersonModal'

export const PersonList: React.FC = () => {
  const { listPeople, deletePerson } = usePeople()
  const [people, setPeople] = useState<Person[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [editingPerson, setEditingPerson] = useState<Person | null>(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  // Use ref to avoid stale closure issues with listPeople
  const listPeopleRef = useRef(listPeople)
  listPeopleRef.current = listPeople

  const loadPeople = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const tags = tagFilter ? tagFilter.split(',').map(t => t.trim()).filter(Boolean) : undefined
      const data = await listPeopleRef.current(tags, searchTerm || undefined)
      setPeople(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load people')
    } finally {
      setLoading(false)
    }
  }, [searchTerm, tagFilter])

  useEffect(() => {
    loadPeople()
  }, [loadPeople])

  const handleDelete = async (identifier: string) => {
    setDeletingId(identifier)
    try {
      await deletePerson(identifier)
      setPeople(prev => prev.filter(p => p.identifier !== identifier))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete person')
    } finally {
      setDeletingId(null)
    }
  }

  const handleEdit = (person: Person) => {
    setEditingPerson(person)
    setIsModalOpen(true)
  }

  const handleCreate = () => {
    setEditingPerson(null)
    setIsModalOpen(true)
  }

  const handleModalClose = () => {
    setIsModalOpen(false)
    setEditingPerson(null)
  }

  const handleModalSuccess = () => {
    setIsModalOpen(false)
    setEditingPerson(null)
    loadPeople()
  }

  const allTags = React.useMemo(() => {
    const tagSet = new Set<string>()
    people.forEach(p => p.tags?.forEach(t => tagSet.add(t)))
    return Array.from(tagSet).sort()
  }, [people])

  return (
    <div className="min-h-screen bg-slate-50 p-4 md:p-8">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-4">
            <Link
              to="/ui/dashboard"
              className="text-slate-500 hover:text-slate-700"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
              </svg>
            </Link>
            <h1 className="text-2xl font-semibold text-slate-800">People</h1>
          </div>
          <button
            type="button"
            onClick={handleCreate}
            className="bg-primary text-white py-2 px-4 rounded-lg font-medium hover:bg-primary-dark"
          >
            Add Person
          </button>
        </div>

        {/* Filters */}
        <div className="bg-white rounded-xl shadow-sm p-4 mb-6 space-y-4">
          <div className="flex flex-col sm:flex-row gap-4">
            <div className="flex-1">
              <label htmlFor="search" className="block text-sm font-medium text-slate-700 mb-1">
                Search
              </label>
              <input
                id="search"
                type="text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Search by name, identifier, or notes..."
                className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
              />
            </div>
            <div className="sm:w-64">
              <label htmlFor="tags" className="block text-sm font-medium text-slate-700 mb-1">
                Filter by Tags
              </label>
              <select
                id="tags"
                value={tagFilter}
                onChange={(e) => setTagFilter(e.target.value)}
                className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
              >
                <option value="">All Tags</option>
                {allTags.map(tag => (
                  <option key={tag} value={tag}>{tag}</option>
                ))}
              </select>
            </div>
          </div>
          {(searchTerm || tagFilter) && (
            <div className="flex items-center gap-2 text-sm text-slate-600">
              <span>Showing {people.length} result{people.length !== 1 ? 's' : ''}</span>
              <button
                type="button"
                onClick={() => {
                  setSearchTerm('')
                  setTagFilter('')
                }}
                className="text-primary hover:underline"
              >
                Clear filters
              </button>
            </div>
          )}
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg mb-4">
            {error}
            <button
              type="button"
              onClick={() => setError(null)}
              className="ml-2 underline"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Content */}
        {loading ? (
          <div className="text-center py-8 text-slate-500">Loading people...</div>
        ) : people.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-xl shadow-sm">
            <p className="text-slate-500 mb-4">
              {searchTerm || tagFilter
                ? 'No people found matching your filters.'
                : "You haven't added any people yet."}
            </p>
            {!searchTerm && !tagFilter && (
              <button
                type="button"
                onClick={handleCreate}
                className="inline-block bg-primary text-white py-2 px-4 rounded-lg font-medium hover:bg-primary-dark"
              >
                Add Your First Person
              </button>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {people.map((person) => (
              <PersonCard
                key={person.identifier}
                person={person}
                onEdit={handleEdit}
                onDelete={handleDelete}
                expanded={expandedId === person.identifier}
                onToggleExpand={() =>
                  setExpandedId(
                    expandedId === person.identifier ? null : person.identifier
                  )
                }
                disabled={deletingId === person.identifier}
              />
            ))}
          </div>
        )}

        {/* Modal for create/edit */}
        {isModalOpen && (
          <PersonModal
            person={editingPerson}
            onClose={handleModalClose}
            onSuccess={handleModalSuccess}
          />
        )}
      </div>
    </div>
  )
}

export default PersonList
