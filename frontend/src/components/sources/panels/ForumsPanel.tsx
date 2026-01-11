import { useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { styles } from '../styles'

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
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Forums (LessWrong)</h3>
      </div>

      <div className={styles.configBox}>
        <h4 className="font-medium text-slate-800 mb-2">Sync Settings</h4>
        <p className="text-sm text-slate-600 mb-4">
          Configure and trigger synchronization of posts from LessWrong. Posts matching your criteria will be indexed for search.
        </p>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Days Back</label>
            <input
              type="number"
              value={daysBack}
              onChange={e => setDaysBack(parseInt(e.target.value) || 30)}
              min={1}
              max={365}
              className={styles.formInput}
            />
          </div>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Min Karma</label>
            <input
              type="number"
              value={minKarma}
              onChange={e => setMinKarma(parseInt(e.target.value) || 0)}
              min={0}
              className={styles.formInput}
            />
          </div>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Posts per request</label>
            <input
              type="number"
              value={limit}
              onChange={e => setLimit(parseInt(e.target.value) || 50)}
              min={1}
              max={100}
              className={styles.formInput}
            />
          </div>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Max Items</label>
            <input
              type="number"
              value={maxItems}
              onChange={e => setMaxItems(parseInt(e.target.value) || 1000)}
              min={1}
              max={10000}
              className={styles.formInput}
            />
          </div>
        </div>

        <div className="flex items-center gap-2 mb-4">
          <input
            type="checkbox"
            id="af-only"
            checked={af}
            onChange={e => setAf(e.target.checked)}
            className="rounded border-slate-300"
          />
          <label htmlFor="af-only" className="text-sm text-slate-700">
            Alignment Forum only
          </label>
        </div>

        <button
          className={styles.btnPrimary}
          onClick={handleSync}
          disabled={syncing}
        >
          {syncing ? 'Syncing...' : 'Sync LessWrong'}
        </button>

        {syncError && <div className={`${styles.errorBanner} mt-4`}>{syncError}</div>}
        {syncSuccess && <div className={`${styles.successBanner} mt-4`}>{syncSuccess}</div>}
      </div>
    </div>
  )
}

export default ForumsPanel
