import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import UserSelector, { useUserSelection } from '../common/UserSelector'
import { useAuth } from '../../hooks/useAuth'
import { SourceTabCounts, useSources } from '../../hooks/useSources'
import { SourcesContext } from './context'
import { AccountsPanel } from './panels/AccountsPanel'
import { EmailPanel } from './panels/EmailPanel'
import { FeedsPanel } from './panels/FeedsPanel'
import { GitHubPanel } from './panels/GitHubPanel'
import { GoogleDrivePanel } from './panels/GoogleDrivePanel'
import { CalendarPanel } from './panels/CalendarPanel'
import { TranscriptsPanel } from './panels/TranscriptsPanel'
import { BooksPanel } from './panels/BooksPanel'
import { ForumsPanel } from './panels/ForumsPanel'
import { PhotosPanel } from './panels/PhotosPanel'
import { NotesPanel } from './panels/NotesPanel'
import { SecretsPanel } from './panels/SecretsPanel'
import { DiscordPanel } from './panels/DiscordPanel'
import { SlackPanel } from './panels/SlackPanel'
import { Page, Tabs } from '@/components/shared/ui'

const TABS = [
  { value: 'accounts', label: 'Accounts', panel: AccountsPanel },
  { value: 'email', label: 'Email', panel: EmailPanel },
  { value: 'feeds', label: 'RSS feeds', panel: FeedsPanel },
  { value: 'github', label: 'GitHub', panel: GitHubPanel },
  { value: 'drive', label: 'Drive', panel: GoogleDrivePanel },
  { value: 'calendar', label: 'Calendar', panel: CalendarPanel },
  { value: 'transcripts', label: 'Transcripts', panel: TranscriptsPanel },
  { value: 'books', label: 'Books', panel: BooksPanel },
  { value: 'forums', label: 'Forums', panel: ForumsPanel },
  { value: 'photos', label: 'Photos', panel: PhotosPanel },
  { value: 'notes', label: 'Notes', panel: NotesPanel },
  { value: 'discord', label: 'Discord', panel: DiscordPanel },
  { value: 'slack', label: 'Slack', panel: SlackPanel },
  { value: 'secrets', label: 'Secrets', panel: SecretsPanel },
] as const
type TabType = typeof TABS[number]['value']

const Sources = () => {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab')
  const activeTab: TabType = TABS.find(t => t.value === tabParam)?.value ?? 'accounts'
  const setActiveTab = (tab: TabType) => setSearchParams(tab === 'accounts' ? {} : { tab })
  const ActivePanel = TABS.find(t => t.value === activeTab)!.panel
  const [selectedUser, setSelectedUser] = useUserSelection('sourcesSelectedUser')
  const { hasScope } = useAuth()

  const { getSourceTabCounts } = useSources()
  const [counts, setCounts] = useState<SourceTabCounts>({})
  const latestRequest = useRef(0)

  // Stable per selected user, or panels that load with it would reload in a loop.
  const refreshCounts = useCallback(() => {
    const request = ++latestRequest.current
    // Panels call this too, so a counter rather than an effect-cleanup flag drops every superseded response.
    // On a failed fetch the previous badges stay up rather than all vanishing.
    getSourceTabCounts(selectedUser.id || undefined)
      .then(next => { if (request === latestRequest.current) setCounts(next) })
      .catch(() => {})
  }, [getSourceTabCounts, selectedUser.id])

  useEffect(refreshCounts, [refreshCounts])

  const tabOptions = TABS.map(({ value, label }) => {
    const failing = counts[value]?.failing
    return { value, label, count: counts[value]?.count, problem: failing ? `${failing} failing to sync` : undefined }
  })

  // Check if user is admin
  const isAdmin = hasScope('admin') || hasScope('*')

  // Compute userId for API calls
  const userId = selectedUser.id || undefined

  return (
    <SourcesContext.Provider value={{ selectedUser, userId, refreshCounts }}>
      <Page
        title={activeTab === 'secrets' ? 'Secrets' : 'Sources'}
        actions={isAdmin && <UserSelector value={selectedUser} onChange={setSelectedUser} onlyHumanUsers />}
        width="wide"
      >
        <Tabs label="Source types" options={tabOptions} value={activeTab} onChange={setActiveTab}>
          <ActivePanel />
        </Tabs>
      </Page>
    </SourcesContext.Provider>
  )
}

export default Sources
