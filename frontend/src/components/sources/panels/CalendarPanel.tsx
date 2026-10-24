import { useState, useEffect, useCallback } from 'react'
import { useSources, CalendarAccount, GoogleAccount } from '@/hooks/useSources'
import { useProjects, Project } from '@/hooks/useProjects'
import { useCalendar, CalendarEvent } from '@/hooks/useCalendar'
import { SourceRow, TagsInput, IntervalInput } from '../shared'
import { Badge, Button, Disclosure, EmptyState, List, Row, Section, ErrorState, Field, FieldRow, FormActions, Hint, Input, Label, LoadingState, Modal, Notice, Select, useActionNotice } from '@/components/shared/ui'
import { useSourcesContext } from '../Sources'

interface GroupedEvents {
  [calendarName: string]: CalendarEvent[]
}

export const CalendarPanel = () => {
  const {
    listCalendarAccounts, createCalendarAccount, updateCalendarAccount,
    deleteCalendarAccount, syncCalendarAccount, listGoogleAccounts
  } = useSources()
  const { listProjects } = useProjects()
  const { userId } = useSourcesContext()
  const { getUpcomingEvents } = useCalendar()
  const [accounts, setAccounts] = useState<CalendarAccount[]>([])
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccount[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingAccount, setEditingAccount] = useState<CalendarAccount | null>(null)
  const { run, notice } = useActionNotice()

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // Calculate date range: 30 days in past to 90 days in future
      const startDate = new Date()
      startDate.setDate(startDate.getDate() - 30)
      const endDate = new Date()
      endDate.setDate(endDate.getDate() + 90)

      const [calendarData, googleData, projectData, eventsData] = await Promise.all([
        listCalendarAccounts(userId),
        listGoogleAccounts(userId),
        listProjects(),
        getUpcomingEvents({
          startDate: startDate.toISOString(),
          endDate: endDate.toISOString(),
          limit: 500,
          userIds: userId ? [userId] : undefined
        })
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

  const handleDelete = (id: number) => run(async () => {
    await deleteCalendarAccount(id)
    loadData()
  })

  const handleToggleActive = (account: CalendarAccount) => run(async () => {
    await updateCalendarAccount(account.id, { active: !account.active })
    loadData()
  })

  const handleSync = (id: number) => run(async () => {
    await syncCalendarAccount(id)
    loadData()
  }, 'Sync queued')

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
    <Section title="Calendar Accounts" actions={<Button variant="primary" onClick={() => setShowForm(true)}>Add Calendar</Button>}>
      {notice}
      {accounts.length === 0 ? (
        <EmptyState
          message="No calendar accounts configured"
          actionLabel="Add Calendar"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <List>
          {accounts.map(account => {
            const accountEvents = Object.entries(getEventsForAccount(account.id))
            return (
              <SourceRow
                key={account.id}
                title={account.name}
                subtitle={account.calendar_type === 'google'
                  ? `Google Calendar (${account.google_account?.email || 'linked'})`
                  : `CalDAV: ${account.caldav_url}`}
                active={account.active}
                lastSyncAt={account.last_sync_at}
                syncError={account.sync_error}
                onToggleActive={() => handleToggleActive(account)}
                onEdit={() => setEditingAccount(account)}
                onDelete={() => handleDelete(account.id)}
                onSync={() => handleSync(account.id)}
              >
                {accountEvents.length === 0 ? (
                  <Hint>No events synced yet</Hint>
                ) : accountEvents.map(([calendarName, calEvents]) => (
                  <Disclosure key={calendarName} summary={<>{calendarName} <span className="font-normal text-slate-500">· {calEvents.length} events</span></>}>
                    <List flush>
                      {calEvents.map((event, idx) => (
                        <Row
                          key={`${event.id}-${idx}`}
                          className="px-0"
                          title={event.event_title}
                          badges={event.recurrence_rule && <Badge tone="primary">Recurring</Badge>}
                          meta={[
                            formatEventDate(event.start_time),
                            event.all_day ? 'All day' : formatEventTime(event.start_time),
                            event.location,
                          ]}
                        />
                      ))}
                    </List>
                  </Disclosure>
                ))}
              </SourceRow>
            )
          })}
        </List>
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
    </Section>
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
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Name">
          <Input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required
            placeholder="My Calendar"
          />
        </Field>

        <Field label="Calendar Type">
          <Select
            value={formData.calendar_type}
            onChange={e => setFormData({ ...formData, calendar_type: e.target.value as 'caldav' | 'google' })}
            disabled={!!account}
          >
            <option value="caldav">CalDAV (Radicale, Nextcloud, etc.)</option>
            <option value="google">Google Calendar</option>
          </Select>
        </Field>

        {formData.calendar_type === 'caldav' ? (
          <>
            <Field label="CalDAV Server URL">
              <Input
                type="url"
                value={formData.caldav_url}
                onChange={e => setFormData({ ...formData, caldav_url: e.target.value })}
                required={!account}
                placeholder="https://caldav.example.com/user/calendar/"
              />
            </Field>

            <Field label="Username">
              <Input
                type="text"
                value={formData.caldav_username}
                onChange={e => setFormData({ ...formData, caldav_username: e.target.value })}
                required={!account}
              />
            </Field>

            <Field label={<>
                Password {account && <span className="text-slate-500">(leave blank to keep current)</span>}</>}>
              <Input
                type="password"
                value={formData.caldav_password}
                onChange={e => setFormData({ ...formData, caldav_password: e.target.value })}
                required={!account}
              />
            </Field>
          </>
        ) : (
          <Field>
            <Label>Google Account</Label>
            {googleAccounts.length === 0 ? (
              <Hint>
                No Google accounts connected. Add a Google account in the Accounts tab first.
              </Hint>
            ) : (
              <Select
                value={formData.google_account_id || ''}
                onChange={e => setFormData({ ...formData, google_account_id: parseInt(e.target.value) || undefined })}
                required
              >
                <option value="">Select a Google account...</option>
                {googleAccounts.map(ga => (
                  <option key={ga.id} value={ga.id}>{ga.email}</option>
                ))}
              </Select>
            )}
          </Field>
        )}

        <FieldRow>
          <Field label="Sync Past Days">
            <Input
              type="number"
              value={formData.sync_past_days}
              onChange={e => setFormData({ ...formData, sync_past_days: parseInt(e.target.value) || 30 })}
              min={0}
              max={365}
            />
          </Field>
          <Field label="Sync Future Days">
            <Input
              type="number"
              value={formData.sync_future_days}
              onChange={e => setFormData({ ...formData, sync_future_days: parseInt(e.target.value) || 90 })}
              min={0}
              max={365}
            />
          </Field>
        </FieldRow>

        <IntervalInput
          value={formData.check_interval}
          onChange={check_interval => setFormData({ ...formData, check_interval })}
          label="Check interval"
        />

        <Field label="Tags">
          <TagsInput
            tags={formData.tags}
            onChange={tags => setFormData({ ...formData, tags })}
          />
        </Field>

        <FieldRow>
          <Field label="Project" hint="Project for access control">
            <Select
              value={formData.project_id || ''}
              onChange={e => setFormData({ ...formData, project_id: e.target.value ? parseInt(e.target.value) : undefined })}
            >
              <option value="">None</option>
              {projects.map(project => (
                <option key={project.id} value={project.id}>
                  {project.title} ({project.repo_path})
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Sensitivity" hint="Visibility level for calendar events">
            <Select
              value={formData.sensitivity}
              onChange={e => setFormData({ ...formData, sensitivity: e.target.value as 'public' | 'basic' | 'internal' | 'confidential' })}
            >
              <option value="public">Public</option>
              <option value="basic">Basic</option>
              <option value="internal">Internal</option>
              <option value="confidential">Confidential</option>
            </Select>
          </Field>
        </FieldRow>

        <FormActions>
          <Button type="button" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default CalendarPanel
