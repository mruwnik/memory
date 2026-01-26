import { useState, useEffect, useCallback } from 'react'
import { useSources, ArticleFeed } from '@/hooks/useSources'
import {
  SourceCard,
  Modal,
  TagsInput,
  IntervalInput,
  EmptyState,
  LoadingState,
  ErrorState,
} from '../shared'
import { styles } from '../styles'
import { useSourcesContext } from '../Sources'

export const FeedsPanel = () => {
  const { listArticleFeeds, createArticleFeed, updateArticleFeed, deleteArticleFeed, syncArticleFeed } = useSources()
  const { userId } = useSourcesContext()
  const [feeds, setFeeds] = useState<ArticleFeed[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingFeed, setEditingFeed] = useState<ArticleFeed | null>(null)

  const loadFeeds = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listArticleFeeds(userId)
      setFeeds(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load feeds')
    } finally {
      setLoading(false)
    }
  }, [listArticleFeeds, userId])

  useEffect(() => { loadFeeds() }, [loadFeeds])

  const handleCreate = async (data: any) => {
    try {
      await createArticleFeed(data)
      setShowForm(false)
      loadFeeds()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create feed')
    }
  }

  const handleUpdate = async (data: any) => {
    if (editingFeed) {
      try {
        await updateArticleFeed(editingFeed.id, data)
        setEditingFeed(null)
        loadFeeds()
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to update feed')
      }
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteArticleFeed(id)
      loadFeeds()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete feed')
    }
  }

  const handleToggleActive = async (feed: ArticleFeed) => {
    try {
      await updateArticleFeed(feed.id, { active: !feed.active })
      loadFeeds()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to toggle feed status')
    }
  }

  const handleSync = async (id: number) => {
    try {
      await syncArticleFeed(id)
      loadFeeds()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to sync feed')
    }
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadFeeds} />

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>RSS Feeds</h3>
        <button className={styles.btnAdd} onClick={() => setShowForm(true)}>Add Feed</button>
      </div>

      {feeds.length === 0 ? (
        <EmptyState
          message="No RSS feeds configured"
          actionLabel="Add RSS Feed"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <div className={styles.sourceList}>
          {feeds.map(feed => (
            <SourceCard
              key={feed.id}
              title={feed.title || feed.url}
              subtitle={feed.url}
              active={feed.active}
              lastSyncAt={feed.last_checked_at}
              onToggleActive={() => handleToggleActive(feed)}
              onEdit={() => setEditingFeed(feed)}
              onDelete={() => handleDelete(feed.id)}
              onSync={() => handleSync(feed.id)}
            >
              {feed.description && (
                <p className="text-sm text-slate-500 mt-1">{feed.description}</p>
              )}
            </SourceCard>
          ))}
        </div>
      )}

      {showForm && (
        <FeedForm
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingFeed && (
        <FeedForm
          feed={editingFeed}
          onSubmit={handleUpdate}
          onCancel={() => setEditingFeed(null)}
        />
      )}
    </div>
  )
}

interface FeedFormProps {
  feed?: ArticleFeed
  onSubmit: (data: any) => Promise<void>
  onCancel: () => void
}

const FeedForm = ({ feed, onSubmit, onCancel }: FeedFormProps) => {
  const [formData, setFormData] = useState({
    url: feed?.url || '',
    title: feed?.title || '',
    description: feed?.description || '',
    tags: feed?.tags || [],
    check_interval: feed?.check_interval || 1440,
    active: feed?.active ?? true,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(formData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={feed ? 'Edit RSS Feed' : 'Add RSS Feed'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Feed URL</label>
          <input
            type="url"
            value={formData.url}
            onChange={e => setFormData({ ...formData, url: e.target.value })}
            required
            disabled={!!feed}
            placeholder="https://example.com/feed.xml"
            className={styles.formInput}
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Title (optional)</label>
          <input
            type="text"
            value={formData.title}
            onChange={e => setFormData({ ...formData, title: e.target.value })}
            className={styles.formInput}
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Description (optional)</label>
          <textarea
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
            rows={2}
            className={styles.formTextarea}
          />
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

export default FeedsPanel
