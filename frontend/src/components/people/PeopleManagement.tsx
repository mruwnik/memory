import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useAuth } from '../../hooks/useAuth'
import { usePeople, type Person, type PersonCreate, type PersonUpdate, type Tidbit, type TidbitInput } from '../../hooks/usePeople'
import { useModule } from '../../hooks/useModule'
import { useTeams, type Team } from '../../hooks/useTeams'
import { useDebounce } from '../../hooks/useDebounce'
import PersonCard from './PersonCard'
import PersonFormModal from './PersonFormModal'
import TidbitFormModal from './TidbitFormModal'
import { Button, Checkbox, EmptyState, Input, Label, List, Modal, MultiSelect, Notice, Page, RefreshButton, Select, Toolbar, useActionNotice } from '@/components/shared/ui'

const PAGE_LIMIT = 200

const PeopleManagement = () => {
  const { listPeople, addPerson, updatePerson, deletePerson, getPerson, mergePeople, addTidbit, updateTidbit, deleteTidbit } = usePeople()
  const { getPersonTeams, listTeams, listMembers } = useTeams()
  const { user, hasScope } = useAuth()
  const teamsOn = useModule('teams')

  const [people, setPeople] = useState<Person[]>([])
  const [capped, setCapped] = useState(false)
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


  // Tidbit modal: adding to a person, or editing one of their tidbits
  const [tidbitForm, setTidbitForm] = useState<{ identifier: string; tidbit?: Tidbit } | null>(null)
  const [tidbitLoading, setTidbitLoading] = useState(false)
  const [tidbitError, setTidbitError] = useState<string | null>(null)
  const { run, notice, clear: clearNotice } = useActionNotice()
  const reloadTimers = useRef(new Set<ReturnType<typeof setTimeout>>())
  useEffect(() => {
    const timers = reloadTimers.current
    return () => timers.forEach(clearTimeout)
  }, [])

  // Merge state
  const [selectedForMerge, setSelectedForMerge] = useState<Set<string>>(new Set())
  const [showMergeModal, setShowMergeModal] = useState(false)
  const [mergeLoading, setMergeLoading] = useState(false)
  const [mergePrimaryId, setMergePrimaryId] = useState<string | null>(null)
  const [mergeError, setMergeError] = useState<string | null>(null)

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
        limit: PAGE_LIMIT,
      })
      setCapped(data.length >= PAGE_LIMIT)

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
    if (!teamsOn) return
    const loadAllTeams = async () => {
      try {
        const teams = await listTeams({ include_inactive: false })
        setAllTeams(teams)
      } catch {
        // Silently fail - team filter just won't appear
      }
    }
    loadAllTeams()
  }, [listTeams, teamsOn])

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

  const handleDelete = async (person: Person) => {
    try {
      await deletePerson(person.identifier)
      await loadPeople()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete person')
    }
  }

  const reloadDetails = async (identifier: string) => {
    const details = await getPerson(identifier, true)
    if (details) setPersonDetails(prev => ({ ...prev, [identifier]: details }))
  }

  const addPendingTidbit = (identifier: string, data: TidbitInput) =>
    setPersonDetails(prev => {
      const details = prev[identifier]
      if (!details) return prev
      const pending: Tidbit = {
        ...data, id: -Date.now(), person_id: details.id, source: null, project_id: null,
        creator_id: user?.id ?? null, created_at: new Date().toISOString(),
      }
      return { ...prev, [identifier]: { ...details, tidbits: [...(details.tidbits ?? []), pending] } }
    })

  const canEditTidbit = (tidbit: Tidbit) =>
    tidbit.id > 0 && (hasScope('*') || (user != null && tidbit.creator_id === user.id))

  const handleTidbitSubmit = async (data: TidbitInput) => {
    if (!tidbitForm) return
    const { identifier, tidbit } = tidbitForm
    setTidbitLoading(true)
    setTidbitError(null)
    try {
      await (tidbit ? updateTidbit(tidbit.id, data) : addTidbit(identifier, data))
      setTidbitForm(null)
      if (tidbit) return await reloadDetails(identifier)
      // A new tidbit is written by a background task: show it now (id < 0, no actions yet) and reconcile later.
      addPendingTidbit(identifier, data)
      const timer = setTimeout(() => {
        reloadTimers.current.delete(timer)
        reloadDetails(identifier)
      }, 1000)
      reloadTimers.current.add(timer)
    } catch (e) {
      setTidbitError(e instanceof Error ? e.message : 'Failed to save tidbit')
    } finally {
      setTidbitLoading(false)
    }
  }

  const handleTidbitDelete = (identifier: string, tidbit: Tidbit) =>
    run(async () => {
      await deleteTidbit(tidbit.id)
      await reloadDetails(identifier)
    })

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
        setMergeError(result.error || 'Failed to merge people')
      }
    } catch (e) {
      setMergeError(e instanceof Error ? e.message : 'Failed to merge people')
    } finally {
      setMergeLoading(false)
    }
  }

  const toggleExpanded = async (identifier: string) => {
    const isExpanding = expandedIdentifier !== identifier
    setExpandedIdentifier(prev => prev === identifier ? null : identifier)

    if (!isExpanding) return

    // Fetch teams when expanding, if not already loaded
    if (teamsOn && !personTeams[identifier] && !teamsLoading[identifier]) {
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

  return (
    <>
      <Page
        title="People"
        description={people.length > 0 && `Showing ${people.length} ${people.length === 1 ? 'person' : 'people'}`
          + (capped ? `, from the first ${PAGE_LIMIT} loaded; search to find others` : '')}
        actions={<Button variant="primary" onClick={() => setShowCreateModal(true)}>New person</Button>}
        width="default"
        loading={loading && people.length === 0}
      >
        {error && <Notice tone="error" onDismiss={() => setError(null)}>{error}</Notice>}
        {notice}

        <Toolbar
          selected={selectedForMerge.size}
          onClearSelection={clearMergeSelection}
          actions={<>
            {selectedForMerge.size === 1 && <span className="text-xs text-slate-500">Select one more person to merge</span>}
            {selectedForMerge.size >= 2 && (
              <Button onClick={() => {
                setMergePrimaryId(Array.from(selectedForMerge)[0])
                setMergeError(null)
                setShowMergeModal(true)
              }}>Merge</Button>
            )}
            <RefreshButton onClick={loadPeople} loading={loading} label="Refresh people" />
          </>}
        >
          <Input type="search" placeholder="Search by name, alias, or notes..." aria-label="Search people" value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)} className="sm:w-72" />
          {teamsOn && allTeams.length > 0 && (
            <Select className="w-auto" aria-label="Team" value={teamFilter ?? ''}
              onChange={e => setTeamFilter(e.target.value ? Number(e.target.value) : null)}>
              <option value="">All teams</option>
              {allTeams.map(team => (
                <option key={team.id} value={team.id}>{team.name}{team.member_count !== undefined ? ` (${team.member_count})` : ''}</option>
              ))}
            </Select>
          )}
          {allTags.length > 0 && (
            <MultiSelect options={allTags} value={tagFilter} onChange={setTagFilter} placeholder="Any tag" aria-label="Filter by tag" className="sm:w-64" />
          )}
        </Toolbar>

        {people.length === 0 ? (
          <EmptyState message={searchTerm || tagFilter.length > 0
            ? 'No people found matching your search.'
            : 'No people tracked yet. Add your first person above.'} />
        ) : (
          <List>
            {people.map((person) => (
              <PersonCard
                key={person.identifier}
                person={person}
                select={
                  <Checkbox checked={selectedForMerge.has(person.identifier)} onChange={() => toggleMergeSelection(person.identifier)}
                    label={<span className="sr-only">Select {person.display_name} for merge</span>} className="p-2 -m-2" />
                }
                expanded={expandedIdentifier === person.identifier}
                onToggleExpand={() => toggleExpanded(person.identifier)}
                onEdit={() => setEditingPerson(person)}
                onDelete={() => handleDelete(person)}
                teams={personTeams[person.identifier]}
                teamsLoading={teamsLoading[person.identifier]}
                tidbits={personDetails[person.identifier]?.tidbits}
                tidbitsLoading={detailsLoading[person.identifier]}
                onAddTidbit={() => { clearNotice(); setTidbitError(null); setTidbitForm({ identifier: person.identifier }) }}
                canEditTidbit={canEditTidbit}
                onEditTidbit={tidbit => { clearNotice(); setTidbitError(null); setTidbitForm({ identifier: person.identifier, tidbit }) }}
                onDeleteTidbit={tidbit => handleTidbitDelete(person.identifier, tidbit)}
              />
            ))}
          </List>
        )}
      </Page>

      {/* Create Person Modal */}
      {showCreateModal && (
        <PersonFormModal
          title="New person"
          onSubmit={handleCreate}
          onCancel={() => {
            setShowCreateModal(false)
            setCreateError(null)
          }}
          loading={createLoading}
          error={createError}
          submitLabel="Create person"
        />
      )}

      {/* Edit Person Modal */}
      {editingPerson && (
        <PersonFormModal
          title="Edit person"
          initialData={editingPerson}
          onSubmit={handleUpdate}
          onCancel={() => {
            setEditingPerson(null)
            setEditError(null)
          }}
          loading={editLoading}
          error={editError}
          submitLabel="Save changes"
          isEdit
        />
      )}

      {tidbitForm && (
        <TidbitFormModal
          title={tidbitForm.tidbit ? 'Edit tidbit' : 'Add tidbit'}
          initialData={tidbitForm.tidbit}
          onSubmit={handleTidbitSubmit}
          onCancel={() => setTidbitForm(null)}
          loading={tidbitLoading}
          error={tidbitError}
          submitLabel={tidbitForm.tidbit ? 'Save changes' : 'Add tidbit'}
        />
      )}

      {/* Merge Modal */}
      {showMergeModal && (
        <Modal title="Merge people" onClose={() => setShowMergeModal(false)}>
          <p className="text-sm text-slate-600 mb-4">
              Merge {selectedForMerge.size} people into one. All tidbits, team memberships, and other
              associations will be moved to the primary person. The other records will be deleted.
            </p>

            <div className="mb-4">
              <Label className="mb-2">
                Select primary person (to keep):
              </Label>
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {Array.from(selectedForMerge).map(identifier => {
                  const person = people.find(p => p.identifier === identifier)
                  return (
                    <label
                      key={identifier}
                      className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                        mergePrimaryId === identifier
                          ? 'bg-primary/10 border-primary/30'
                          : 'border-slate-200 hover:bg-slate-50'
                      }`}
                    >
                      <input
                        type="radio"
                        name="primary"
                        checked={mergePrimaryId === identifier}
                        onChange={() => setMergePrimaryId(identifier)}
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

            <Notice tone="warning" className="mb-4">
              <div className="flex items-start gap-2">
                <svg className="w-5 h-5 text-amber-600 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <div className="text-sm text-amber-800">
                  <strong>This action cannot be undone.</strong> The secondary person records will be permanently deleted.
                  Their identifiers and names will be preserved as aliases on the primary person.
                </div>
              </div>
            </Notice>

            {mergeError && <Notice tone="error" className="mb-4">{mergeError}</Notice>}

            <div className="flex justify-end gap-3">
              <Button
                onClick={() => setShowMergeModal(false)}
              >
                Cancel
              </Button>
              <Button variant="primary"
                onClick={handleMerge}
                disabled={mergeLoading || !mergePrimaryId}
              >
                {mergeLoading ? 'Merging...' : 'Merge people'}
              </Button>
            </div>
        </Modal>
      )}
    </>
  )
}

export default PeopleManagement
