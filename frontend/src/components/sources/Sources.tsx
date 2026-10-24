import { useState, useEffect, createContext, useContext } from 'react'
import { useSearchParams } from 'react-router-dom'
import UserSelector, { SelectedUser, useUserSelection } from '../common/UserSelector'
import { useAuth } from '../../hooks/useAuth'

// Context to provide selected user to all panels
interface SourcesContextType {
  selectedUser: SelectedUser
  userId: number | undefined
}

const SourcesContext = createContext<SourcesContextType>({
  selectedUser: { type: 'user', id: 0, name: '' },
  userId: undefined,
})

export const useSourcesContext = () => useContext(SourcesContext)

// Import all panels
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
  { value: 'discord', label: 'Discord', panel: DiscordPanel },
  { value: 'slack', label: 'Slack', panel: SlackPanel },
  { value: 'secrets', label: 'Secrets', panel: SecretsPanel },
] as const
type TabType = typeof TABS[number]['value']

const Sources = () => {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab')
  const initialTab = TABS.find(t => t.value === tabParam)?.value ?? 'accounts'
  const [activeTab, setActiveTab] = useState<TabType>(initialTab)
  const ActivePanel = TABS.find(t => t.value === activeTab)!.panel
  const [selectedUser, setSelectedUser] = useUserSelection('sourcesSelectedUser')
  const { hasScope } = useAuth()

  // Check if user is admin
  const isAdmin = hasScope('admin') || hasScope('*')

  // Compute userId for API calls
  const userId = selectedUser.id || undefined

  // Update URL when tab changes
  useEffect(() => {
    if (activeTab !== 'accounts') {
      setSearchParams({ tab: activeTab })
    } else {
      setSearchParams({})
    }
  }, [activeTab, setSearchParams])

  return (
    <SourcesContext.Provider value={{ selectedUser, userId }}>
      <Page
        title={activeTab === 'secrets' ? 'Secrets' : 'Sources'}
        actions={isAdmin && <UserSelector value={selectedUser} onChange={setSelectedUser} onlyHumanUsers />}
        width="wide"
      >
        <Tabs label="Source types" options={[...TABS]} value={activeTab} onChange={setActiveTab}>
          <ActivePanel />
        </Tabs>
      </Page>
    </SourcesContext.Provider>
  )
}

export default Sources
