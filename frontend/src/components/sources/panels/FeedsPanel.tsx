import { useState, useEffect, useCallback } from 'react'
import { useSources, ArticleFeed, LLMKeyChoice } from '@/hooks/useSources'
import { SourceRow, TagsInput, IntervalInput } from '../shared'
import { Button, EmptyState, ErrorState, Field, FormActions, Input, List, LoadingState, Modal, Notice, Section, Textarea, useActionNotice } from '@/components/shared/ui'
import { useSourcesContext } from '../context'
import { LLMKeyField, llmKeyLabel } from '../LLMKeyField'

export const FeedsPanel = () => {
  const { listArticleFeeds, createArticleFeed, updateArticleFeed, deleteArticleFeed, syncArticleFeed } = useSources()
  const { userId, refreshCounts } = useSourcesContext()
  const [feeds, setFeeds] = useState<ArticleFeed[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingFeed, setEditingFeed] = useState<ArticleFeed | null>(null)

  const loadFeeds = useCallback(async () => {
    try {
      const data = await listArticleFeeds(userId)
      setFeeds(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load feeds')
    } finally {
      setLoading(false)
      refreshCounts()
    }
  }, [listArticleFeeds, userId, refreshCounts])

  useEffect(() => { loadFeeds() }, [loadFeeds])

  const { run, notice } = useActionNotice()

  // Form handlers throw, so the open modal shows the failure and keeps the input.
  const handleCreate = async (data: any) => {
    await createArticleFeed(data)
    setShowForm(false)
    loadFeeds()
  }

  const handleUpdate = async (data: any) => {
    if (!editingFeed) return
    await updateArticleFeed(editingFeed.id, data)
    setEditingFeed(null)
    loadFeeds()
  }

  const handleDelete = (id: number) => run(async () => {
    await deleteArticleFeed(id)
    loadFeeds()
  })

  const handleToggleActive = (feed: ArticleFeed) => run(async () => {
    await updateArticleFeed(feed.id, { active: !feed.active })
    loadFeeds()
  })

  const handleSync = (id: number) => run(async () => {
    await syncArticleFeed(id)
    loadFeeds()
  })

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadFeeds} />

  return (
    <Section title="RSS feeds" actions={<Button variant="primary" onClick={() => setShowForm(true)}>Add feed</Button>}>
      {notice}
      {feeds.length === 0 ? (
        <EmptyState
          message="No RSS feeds configured"
          actionLabel="Add RSS feed"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <List>
          {feeds.map(feed => (
            <SourceRow
              key={feed.id}
              title={feed.title || feed.url}
              subtitle={feed.url}
              active={feed.active}
              lastSyncAt={feed.last_checked_at}
              lastSyncLabel="Last checked"
              sync={feed}
              meta={[`LLM: ${llmKeyLabel(feed.llm_key)}`]}
              onToggleActive={() => handleToggleActive(feed)}
              onEdit={() => setEditingFeed(feed)}
              onDelete={() => handleDelete(feed.id)}
              onSync={() => handleSync(feed.id)}
            >
              {feed.description && (
                <p className="text-sm text-slate-500 mt-1">{feed.description}</p>
              )}
            </SourceRow>
          ))}
        </List>
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
    </Section>
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
    llm_key: undefined as LLMKeyChoice | undefined,
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
    <Modal title={feed ? 'Edit RSS feed' : 'Add RSS feed'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Feed URL">
          <Input
            type="url"
            value={formData.url}
            onChange={e => setFormData({ ...formData, url: e.target.value })}
            required
            disabled={!!feed}
            placeholder="https://example.com/feed.xml"
          />
        </Field>

        <Field label="Title (optional)">
          <Input
            type="text"
            value={formData.title}
            onChange={e => setFormData({ ...formData, title: e.target.value })}
          />
        </Field>

        <Field label="Description (optional)">
          <Textarea
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
            rows={2}
          />
        </Field>

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

        <LLMKeyField value={feed?.llm_key} onChange={llm_key => setFormData({ ...formData, llm_key })} />

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

export default FeedsPanel
