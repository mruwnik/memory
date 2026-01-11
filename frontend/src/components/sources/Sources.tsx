import { useState } from 'react'
import { Link } from 'react-router-dom'

// Import all panels
import { AccountsPanel } from './panels/AccountsPanel'
import { EmailPanel } from './panels/EmailPanel'
import { FeedsPanel } from './panels/FeedsPanel'
import { GitHubPanel } from './panels/GitHubPanel'
import { GoogleDrivePanel } from './panels/GoogleDrivePanel'
import { CalendarPanel } from './panels/CalendarPanel'
import { BooksPanel } from './panels/BooksPanel'
import { ForumsPanel } from './panels/ForumsPanel'
import { PhotosPanel } from './panels/PhotosPanel'

type TabType = 'accounts' | 'email' | 'feeds' | 'github' | 'drive' | 'calendar' | 'books' | 'forums' | 'photos'

const Sources = () => {
  const [activeTab, setActiveTab] = useState<TabType>('accounts')

  const tabClass = (tab: TabType) =>
    `py-2 px-4 text-sm font-medium rounded-t-lg border-b-2 transition-colors ${
      activeTab === tab
        ? 'border-primary text-primary bg-white'
        : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'
    }`

  return (
    <div className="min-h-screen bg-slate-50 p-4 md:p-8">
      <div className="flex items-center gap-4 mb-6">
        <Link to="/ui/dashboard" className="py-2 px-4 bg-slate-100 text-slate-700 rounded-lg text-sm hover:bg-slate-200">Back</Link>
        <h2 className="text-2xl font-semibold text-slate-800">Manage Sources</h2>
      </div>

      <div className="flex flex-wrap gap-1 border-b border-slate-200 mb-6">
        <button className={tabClass('accounts')} onClick={() => setActiveTab('accounts')}>
          Accounts
        </button>
        <button className={tabClass('email')} onClick={() => setActiveTab('email')}>
          Email
        </button>
        <button className={tabClass('feeds')} onClick={() => setActiveTab('feeds')}>
          RSS Feeds
        </button>
        <button className={tabClass('github')} onClick={() => setActiveTab('github')}>
          GitHub
        </button>
        <button className={tabClass('drive')} onClick={() => setActiveTab('drive')}>
          Drive
        </button>
        <button className={tabClass('calendar')} onClick={() => setActiveTab('calendar')}>
          Calendar
        </button>
        <button className={tabClass('books')} onClick={() => setActiveTab('books')}>
          Books
        </button>
        <button className={tabClass('forums')} onClick={() => setActiveTab('forums')}>
          Forums
        </button>
        <button className={tabClass('photos')} onClick={() => setActiveTab('photos')}>
          Photos
        </button>
      </div>

      <div className="space-y-6">
        {activeTab === 'accounts' && <AccountsPanel />}
        {activeTab === 'email' && <EmailPanel />}
        {activeTab === 'feeds' && <FeedsPanel />}
        {activeTab === 'github' && <GitHubPanel />}
        {activeTab === 'drive' && <GoogleDrivePanel />}
        {activeTab === 'calendar' && <CalendarPanel />}
        {activeTab === 'books' && <BooksPanel />}
        {activeTab === 'forums' && <ForumsPanel />}
        {activeTab === 'photos' && <PhotosPanel />}
      </div>
    </div>
  )
}

export default Sources
