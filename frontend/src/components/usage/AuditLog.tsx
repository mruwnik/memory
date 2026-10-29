import { useCallback, useEffect, useRef, useState } from 'react'
import { AuditEvent, AuditResponse, useAudit } from '@/hooks/useAudit'
import { useUsers, User } from '@/hooks/useUsers'
import {
  Badge, Disclosure, EmptyState, ErrorState, List, LoadingState, Pager, RefreshButton, Row, Select, Toolbar,
} from '@/components/shared/ui'
import { formatUserDateTime } from '@/utils/timezones'

const PAGE_SIZE = 50

// The actions record_audit writes, plus the retired snapshot action whose rows remain.
const ACTIONS = ['mcp.call', 'settings.update', 'settings.restart', 'team.external_membership_snapshot']

// No id and no email means no user acted; a deleted user keeps their email.
const actorName = (event: AuditEvent, users: User[]) =>
  users.find(u => u.id === event.actor_id)?.name ?? event.actor_email ?? (event.actor_id === null ? 'System' : `User #${event.actor_id}`)

export const AuditLog = () => {
  const { listAuditEvents } = useAudit()
  const { listUsers } = useUsers()
  const [action, setAction] = useState('')
  const [actorId, setActorId] = useState<number | undefined>(undefined)
  const [page, setPage] = useState(0)
  const [result, setResult] = useState<AuditResponse | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const latest = useRef(0)

  useEffect(() => {
    listUsers().then(setUsers).catch(() => setUsers([]))
  }, [listUsers])

  const load = useCallback(async () => {
    const call = ++latest.current
    setLoading(true)
    setError(null)
    try {
      const res = await listAuditEvents({ action, actorId, limit: PAGE_SIZE, offset: page * PAGE_SIZE })
      if (call === latest.current) setResult(res)
    } catch (err) {
      if (call === latest.current) setError(err instanceof Error ? err.message : 'Failed to load audit events')
    } finally {
      if (call === latest.current) setLoading(false)
    }
  }, [listAuditEvents, action, actorId, page])

  useEffect(() => { load() }, [load])

  const filter = <T,>(set: (v: T) => void) => (v: T) => { set(v); setPage(0) }
  const filtered = !!action || actorId !== undefined

  return (
    <div className="space-y-4">
      <Toolbar actions={<RefreshButton onClick={load} loading={loading} label="Refresh audit log" />}>
        <Select aria-label="Action" className="sm:w-56" value={action} onChange={e => filter(setAction)(e.target.value)}>
          <option value="">All actions</option>
          {ACTIONS.map(a => <option key={a} value={a}>{a}</option>)}
        </Select>
        <Select
          aria-label="Actor"
          className="sm:w-56"
          value={actorId ?? ''}
          onChange={e => filter(setActorId)(e.target.value ? Number(e.target.value) : undefined)}
        >
          <option value="">All actors</option>
          {users.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
        </Select>
      </Toolbar>

      {error ? <ErrorState message={error} onRetry={load} />
        : !result ? <LoadingState message="Loading audit log..." />
        : result.events.length === 0 ? (
          <EmptyState message={filtered
            ? 'No audit events match these filters.'
            : 'No audit events yet. Mutating MCP calls and server settings changes are recorded here.'} />
        ) : (
          <>
            <p className="text-sm text-slate-600">{result.total.toLocaleString()} events</p>
            <List>
              {result.events.map(event => (
                <Row
                  key={event.id}
                  title={event.target ?? event.action}
                  badges={event.target && <Badge tone={event.action.startsWith('settings.') ? 'warning' : 'neutral'}>{event.action}</Badge>}
                  meta={[actorName(event, users), formatUserDateTime(event.timestamp)]}
                >
                  {Object.keys(event.attributes ?? {}).length > 0 && (
                    <Disclosure summary="Details">
                      <pre className="px-4 pb-3 text-xs text-slate-700 whitespace-pre-wrap break-all">
                        {JSON.stringify(event.attributes, null, 2)}
                      </pre>
                    </Disclosure>
                  )}
                </Row>
              ))}
            </List>
            <Pager page={page} hasMore={(page + 1) * PAGE_SIZE < result.total} onPage={setPage} />
          </>
        )}
    </div>
  )
}
