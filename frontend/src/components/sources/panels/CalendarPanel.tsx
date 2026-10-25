import { useState, useEffect, useCallback } from 'react'
import { useSources, CalendarAccount, GoogleAccount, LLMKeyChoice } from '@/hooks/useSources'
import { useModule } from '@/hooks/useModule'
import { useProjects, Project } from '@/hooks/useProjects'
import { useCalendar, CalendarEvent } from '@/hooks/useCalendar'
import { ProjectField, IntervalInput, SensitivitySelect, SourceRow, TagsInput, numberValue } from '../shared'
import { Badge, Button, Disclosure, EmptyState, List, Row, Section, ErrorState, Field, FieldRow, FormActions, Hint, Input, LoadingState, Modal, Notice, Select, useActionNotice } from '@/components/shared/ui'
import { useSourcesContext } from '../Sources'
import { LLMKeyField, llmKeyLabel } from '../LLMKeyField'
import { formatUserDate, formatUserTime } from '@/utils/timezones'

interface GroupedEvents {
  [calendarName: string]: CalendarEvent[]
}

export const CalendarPanel = () => {
  const {
    listCalendarAccounts, createCalendarAccount, updateCalendarAccount,
    deleteCalendarAccount, syncCalendarAccount, listGoogleAccounts
  } = useSources()
  const { listProjects } = useProjects()
  const teamsOn = useModule('teams')
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
    try {
      // Calculate date range: 30 days in past to 90 days in future
      const startDate = new Date()
      startDate.setDate(startDate.getDate() - 30)
      const endDate = new Date()
      endDate.setDate(endDate.getDate() + 90)

      const [calendarData, googleData, projectData, eventsData] = await Promise.all([
        listCalendarAccounts(userId),
        listGoogleAccounts(userId),
        (teamsOn ? listProjects() : Promise.resolve<Project[]>([])),
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
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }, [listCalendarAccounts, listGoogleAccounts, listProjects, teamsOn, getUpcomingEvents, userId])

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
    return formatUserDate(dateStr, { month: 'short', day: 'numeric', year: 'numeric' })
  }

  const formatEventTime = (dateStr: string) => {
    return formatUserTime(dateStr)
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <Section title="Calendar accounts" actions={<Button variant="primary" onClick={() => setShowForm(true)}>Add calendar</Button>}>
      {notice}
      {accounts.length === 0 ? (
        <EmptyState
          message="No calendar accounts configured"
          actionLabel="Add calendar"
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
                sync={account}
                meta={[`LLM: ${llmKeyLabel(account.llm_key)}`]}
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
    sync_past_days: (account?.sync_past_days ?? 30) as number | '',
    sync_future_days: (account?.sync_future_days ?? 90) as number | '',
    project_id: account?.project_id || undefined as number | undefined,
    sensitivity: account?.sensitivity || 'basic' as 'public' | 'basic' | 'internal' | 'confidential',
    llm_key: undefined as LLMKeyChoice | undefined,
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
        ...(formData.llm_key !== undefined ? { llm_key: formData.llm_key } : {}),
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
    <Modal title={account ? 'Edit calendar account' : 'Add calendar account'} onClose={onCancel}>
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

        <Field label="Calendar type">
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
            <Field label="CalDAV server URL">
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
          googleAccounts.length === 0 ? (
            <Hint>
              No Google accounts connected. Connect a Google account first.
            </Hint>
          ) : (
            <Field label="Google account">
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
            </Field>
          )
        )}

        <FieldRow>
          <Field label="Sync past days">
            <Input
              type="number"
              value={formData.sync_past_days}
              onChange={e => setFormData({ ...formData, sync_past_days: numberValue(e.target.value) })}
              required
              min={0}
              max={365}
            />
          </Field>
          <Field label="Sync future days">
            <Input
              type="number"
              value={formData.sync_future_days}
              onChange={e => setFormData({ ...formData, sync_future_days: numberValue(e.target.value) })}
              required
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
          <ProjectField hint="Project for access control" value={formData.project_id} projects={projects}
            onChange={project_id => setFormData({ ...formData, project_id })} />
          <Field label="Sensitivity" hint="Visibility level for calendar events">
            <SensitivitySelect value={formData.sensitivity} onChange={sensitivity => setFormData({ ...formData, sensitivity })} />
          </Field>
        </FieldRow>

        <LLMKeyField value={account?.llm_key} onChange={llm_key => setFormData({ ...formData, llm_key })} />

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
