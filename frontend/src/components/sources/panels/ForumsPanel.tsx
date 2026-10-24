import { useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { Button, Card, Checkbox, Field, FieldRow, FormActions, Input, Notice } from '@/components/shared/ui'

export const ForumsPanel = () => {
  const { apiCall } = useAuth()
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState<string | null>(null)
  const [syncSuccess, setSyncSuccess] = useState<string | null>(null)

  // Sync settings
  const [minKarma, setMinKarma] = useState(10)
  const [limit, setLimit] = useState(50)
  const [maxItems, setMaxItems] = useState(1000)
  const [daysBack, setDaysBack] = useState(30)
  const [af, setAf] = useState(false)

  const handleSync = async () => {
    setSyncing(true)
    setSyncError(null)
    setSyncSuccess(null)

    try {
      const sinceDate = new Date()
      sinceDate.setDate(sinceDate.getDate() - daysBack)

      const response = await apiCall('/forums/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          since: sinceDate.toISOString(),
          min_karma: minKarma,
          limit: limit,
          max_items: maxItems,
          af: af,
          tags: [],
        }),
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Sync failed')
      }

      setSyncSuccess('LessWrong sync started. Posts will be indexed in the background.')
    } catch (e) {
      setSyncError(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setSyncing(false)
    }
  }

  return (
    <Card title="Forums (LessWrong)"
      description="Configure and trigger synchronization of posts from LessWrong. Posts matching your criteria will be indexed for search.">
      <div className="space-y-4">
        <FieldRow className="md:grid-cols-4">
          <Field label="Days Back">
            <Input type="number" value={daysBack} onChange={e => setDaysBack(parseInt(e.target.value) || 30)} min={1} max={365} />
          </Field>
          <Field label="Min Karma">
            <Input type="number" value={minKarma} onChange={e => setMinKarma(parseInt(e.target.value) || 0)} min={0} />
          </Field>
          <Field label="Posts per request">
            <Input type="number" value={limit} onChange={e => setLimit(parseInt(e.target.value) || 50)} min={1} max={100} />
          </Field>
          <Field label="Max Items">
            <Input type="number" value={maxItems} onChange={e => setMaxItems(parseInt(e.target.value) || 1000)} min={1} max={10000} />
          </Field>
        </FieldRow>
        <Checkbox label="Alignment Forum only" checked={af} onChange={e => setAf(e.target.checked)} />
        {syncError && <Notice tone="error">{syncError}</Notice>}
        {syncSuccess && <Notice tone="success">{syncSuccess}</Notice>}
        <FormActions>
          <Button variant="primary" onClick={handleSync} disabled={syncing}>{syncing ? 'Syncing...' : 'Sync LessWrong'}</Button>
        </FormActions>
      </div>
    </Card>
  )
}

export default ForumsPanel
