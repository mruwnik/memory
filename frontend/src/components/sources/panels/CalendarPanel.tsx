import { useState, useEffect, useCallback } from 'react'
import { useSources, CalendarAccount, GoogleAccount, Project } from '@/hooks/useSources'
import { useCalendar, CalendarEvent } from '@/hooks/useCalendar'
import {
  Modal,
  TagsInput,
  IntervalInput,
  EmptyState,
  LoadingState,
  ErrorState,
  StatusBadge,
  SyncStatus,
  SyncButton,
} from '../shared'
import { styles } from '../styles'
import { useSourcesContext } from '../Sources'

interface GroupedEvents {
  [calendarName: string]: CalendarEvent[]
}

export const CalendarPanel = () => {
  const {
    listCalendarAccounts, createCalendarAccount, updateCalendarAccount,
    deleteCalendarAccount, syncCalendarAccount, listGoogleAccounts, listProjects
  } = useSources()
  const { userId } = useSourcesContext()
  const { getUpcomingEvents } = useCalendar()
  const [accounts, setAccounts] = useState<CalendarAccount[]>([])
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccount[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [expandedCalendars, setExpandedCalendars] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<CalendarAccount | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [calendarData, googleData, projectData, eventsData] = await Promise.all([
        listCalendarAccounts(userId),
        listGoogleAccounts(userId),
        listProjects(),
        getUpcomingEvents({ days: 365, limit: 200, userIds: userId ? [userId] : undefined })
      ])
      setAccounts(calendarData)
      setGoogleAccounts(googleData)
      setProjects(projectData)
      setEvents(eventsData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }, [listCalendarAccounts, listGoogleAccounts, listProjects, getUpcomingEvents, userId])

  useEffect(() => { loadData() }, [loadData])

  const handleCreate = async (data: any) => {
    await createCalendarAccount(data)
    setShowForm(false)
    loadData()
  }

  const handleUpdate = async (data: any) => {
    if (editingAccount) {
      await updateCalendarAccount(editingAccount.id, data)
      setEditingAccount(null)
      loadData()
    }
  }

  const handleDelete = async (id: number) => {
    await deleteCalendarAccount(id)
    loadData()
  }

  const handleToggleActive = async (account: CalendarAccount) => {
    await updateCalendarAccount(account.id, { active: !account.active })
    loadData()
  }

  const handleSync = async (id: number) => {
    await syncCalendarAccount(id)
    loadData()
  }

  const toggleCalendar = (calendarName: string) => {
    const newExpanded = new Set(expandedCalendars)
    if (newExpanded.has(calendarName)) {
      newExpanded.delete(calendarName)
    } else {
      newExpanded.add(calendarName)
    }
    setExpandedCalendars(newExpanded)
  }

  const getEventsForAccount = (accountId: number): GroupedEvents => {
    return events
      .filter(event => event.calendar_account_id === accountId)
      .reduce((acc, event) => {
        const calName = event.calendar_name || 'Unknown'
        if (!acc[calName]) acc[calName] = []
        acc[calName].push(event)
        return acc
      }, {} as GroupedEvents)
  }

  const formatEventDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  }

  const formatEventTime = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Calendar Accounts</h3>
        <button className={styles.btnAdd} onClick={() => setShowForm(true)}>Add Calendar</button>
      </div>

      {accounts.length === 0 ? (
        <EmptyState
          message="No calendar accounts configured"
          actionLabel="Add Calendar"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <div className={styles.sourceList}>
          {accounts.map(account => (
            <div key={account.id} className="border border-slate-200 rounded-lg p-4">
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1 min-w-0">
                  <h4 className="font-medium text-slate-800">{account.name}</h4>
                  <p className="text-sm text-slate-500">
                    {account.calendar_type === 'google'
                      ? `Google Calendar (${account.google_account?.email || 'linked'})`
                      : `CalDAV: ${account.caldav_url}`
                    }
                  </p>
                </div>
                <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                  <StatusBadge active={account.active} onClick={() => handleToggleActive(account)} />
                  <SyncButton onSync={() => handleSync(account.id)} disabled={!account.active} label="Sync" />
                  <button className={styles.btnEdit} onClick={() => setEditingAccount(account)}>Edit</button>
                  <button className={styles.btnDelete} onClick={() => handleDelete(account.id)}>Delete</button>
                </div>
              </div>

              <div className="flex flex-wrap gap-3 text-xs text-slate-500 mb-3">
                <span>Type: {account.calendar_type === 'google' ? 'Google Calendar' : 'CalDAV'}</span>
                <SyncStatus lastSyncAt={account.last_sync_at} />
                {account.sync_error && (
                  <span className="text-red-600">Error: {account.sync_error}</span>
                )}
              </div>

              {/* Events grouped by calendar */}
              <div className="mt-4 pt-4 border-t border-slate-100">
                <h5 className="text-sm font-medium text-slate-700 mb-2">Calendars & Events</h5>
                {(() => {
                  const accountEvents = getEventsForAccount(account.id)
                  return Object.keys(accountEvents).length === 0 ? (
                    <p className="text-sm text-slate-400 italic">No events synced yet</p>
                  ) : (
                    <div className="space-y-2">
                      {Object.entries(accountEvents).map(([calendarName, calEvents]) => (
                        <div key={calendarName} className="border border-slate-100 rounded">
                          <button
                            className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-slate-50 transition-colors"
                            onClick={() => toggleCalendar(calendarName)}
                          >
                            <span className="text-slate-400 text-xs">
                              {expandedCalendars.has(calendarName) ? '▼' : '▶'}
                            </span>
                            <span className="flex-1 font-medium text-sm text-slate-700">{calendarName}</span>
                            <span className="text-xs text-slate-400">{calEvents.length} events</span>
                          </button>
                          {expandedCalendars.has(calendarName) && (
                            <div className="px-3 pb-3 space-y-2">
                              {calEvents.map((event, idx) => (
                                <div
                                  key={`${event.id}-${idx}`}
                                  className={`flex gap-4 py-2 border-t border-slate-100 text-sm ${event.all_day ? 'bg-slate-50 -mx-3 px-3' : ''}`}
                                >
                                  <div className="flex-shrink-0 w-24">
                                    <div className="text-slate-700">{formatEventDate(event.start_time)}</div>
                                    {!event.all_day ? (
                                      <div className="text-slate-400 text-xs">{formatEventTime(event.start_time)}</div>
                                    ) : (
                                      <div className="text-xs text-primary font-medium">All day</div>
                                    )}
                                  </div>
                                  <div className="flex-1 min-w-0">
                                    <div className="font-medium text-slate-800 truncate">{event.event_title}</div>
                                    {event.location && <div className="text-xs text-slate-500 truncate">{event.location}</div>}
                                    {event.recurrence_rule && (
                                      <span className="inline-block mt-1 px-1.5 py-0.5 bg-primary/10 text-primary text-xs rounded">
                                        Recurring
                                      </span>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )
                })()}
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <CalendarForm
          googleAccounts={googleAccounts}
          projects={projects}
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingAccount && (
        <CalendarForm
          account={editingAccount}
          googleAccounts={googleAccounts}
          projects={projects}
          onSubmit={handleUpdate}
          onCancel={() => setEditingAccount(null)}
        />
      )}
    </div>
  )
}

interface CalendarFormProps {
  account?: CalendarAccount
  googleAccounts: GoogleAccount[]
  projects: Project[]
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const CalendarForm = ({ account, googleAccounts, projects, onSubmit, onCancel }: CalendarFormProps) => {
  const [formData, setFormData] = useState({
    name: account?.name || '',
    calendar_type: account?.calendar_type || 'caldav' as 'caldav' | 'google',
    caldav_url: account?.caldav_url || '',
    caldav_username: account?.caldav_username || '',
    caldav_password: '',
    google_account_id: account?.google_account_id || undefined as number | undefined,
    tags: account?.tags || [],
    check_interval: account?.check_interval || 15,
    sync_past_days: account?.sync_past_days || 30,
    sync_future_days: account?.sync_future_days || 90,
    project_id: account?.project_id || undefined as number | undefined,
    sensitivity: account?.sensitivity || 'basic' as 'public' | 'basic' | 'internal' | 'confidential',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const data: any = {
        name: formData.name,
        calendar_type: formData.calendar_type,
        tags: formData.tags,
        check_interval: formData.check_interval,
        sync_past_days: formData.sync_past_days,
        sync_future_days: formData.sync_future_days,
        project_id: formData.project_id,
        sensitivity: formData.sensitivity,
      }

      if (formData.calendar_type === 'caldav') {
        data.caldav_url = formData.caldav_url
        data.caldav_username = formData.caldav_username
        if (formData.caldav_password) {
          data.caldav_password = formData.caldav_password
        }
      } else {
        data.google_account_id = formData.google_account_id
      }

      await onSubmit(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={account ? 'Edit Calendar Account' : 'Add Calendar Account'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Name</label>
          <input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
            placeholder="My Calendar"
            className={styles.formInput}
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Calendar Type</label>
          <select
            value={formData.calendar_type}
            onChange={e => setFormData({ ...formData, calendar_type: e.target.value as 'caldav' | 'google' })}
            disabled={!!account}
            className={styles.formSelect}
          >
            <option value="caldav">CalDAV (Radicale, Nextcloud, etc.)</option>
            <option value="google">Google Calendar</option>
          </select>
        </div>

        {formData.calendar_type === 'caldav' ? (
          <>
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>CalDAV Server URL</label>
              <input
                type="url"
                value={formData.caldav_url}
                onChange={e => setFormData({ ...formData, caldav_url: e.target.value })}
                required={!account}
                placeholder="https://caldav.example.com/user/calendar/"
                className={styles.formInput}
              />
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Username</label>
              <input
                type="text"
                value={formData.caldav_username}
                onChange={e => setFormData({ ...formData, caldav_username: e.target.value })}
                required={!account}
                className={styles.formInput}
              />
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                Password {account && <span className="text-slate-400">(leave blank to keep current)</span>}
              </label>
              <input
                type="password"
                value={formData.caldav_password}
                onChange={e => setFormData({ ...formData, caldav_password: e.target.value })}
                required={!account}
                className={styles.formInput}
              />
            </div>
          </>
        ) : (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Google Account</label>
            {googleAccounts.length === 0 ? (
              <p className={styles.formHint}>
                No Google accounts connected. Add a Google account in the Accounts tab first.
              </p>
            ) : (
              <select
                value={formData.google_account_id || ''}
                onChange={e => setFormData({ ...formData, google_account_id: parseInt(e.target.value) || undefined })}
                required
                className={styles.formSelect}
              >
                <option value="">Select a Google account...</option>
                {googleAccounts.map(ga => (
                  <option key={ga.id} value={ga.id}>{ga.email}</option>
                ))}
              </select>
            )}
          </div>
        )}

        <div className={styles.formRow}>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Sync Past Days</label>
            <input
              type="number"
              value={formData.sync_past_days}
              onChange={e => setFormData({ ...formData, sync_past_days: parseInt(e.target.value) || 30 })}
              min={0}
              max={365}
              className={styles.formInput}
            />
          </div>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Sync Future Days</label>
            <input
              type="number"
              value={formData.sync_future_days}
              onChange={e => setFormData({ ...formData, sync_future_days: parseInt(e.target.value) || 90 })}
              min={0}
              max={365}
              className={styles.formInput}
            />
          </div>
        </div>

        <IntervalInput
          value={formData.check_interval}
          onChange={check_interval => setFormData({ ...formData, check_interval })}
          label="Check interval"
        />

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Tags</label>
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </div>

        <div className={styles.formRow}>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Project</label>
            <select
              value={formData.project_id || ''}
              onChange={e => setFormData({ ...formData, project_id: e.target.value ? parseInt(e.target.value) : undefined })}
              className={styles.formSelect}
            >
              <option value="">None</option>
              {projects.map(project => (
                <option key={project.id} value={project.id}>
                  {project.title} ({project.repo_path})
                </option>
              ))}
            </select>
            <p className={styles.formHint}>Project for access control</p>
          </div>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Sensitivity</label>
            <select
              value={formData.sensitivity}
              onChange={e => setFormData({ ...formData, sensitivity: e.target.value as 'public' | 'basic' | 'internal' | 'confidential' })}
              className={styles.formSelect}
            >
              <option value="public">Public</option>
              <option value="basic">Basic</option>
              <option value="internal">Internal</option>
              <option value="confidential">Confidential</option>
            </select>
            <p className={styles.formHint}>Visibility level for calendar events</p>
          </div>
        </div>

        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onCancel}>Cancel</button>
          <button type="submit" className={styles.btnSubmit} disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default CalendarPanel
