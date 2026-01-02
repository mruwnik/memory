import { useCallback } from 'react'
import { useAuth, SERVER_URL } from './useAuth'

// Types for Email Accounts
export interface EmailAccount {
  id: number
  name: string
  email_address: string
  imap_server: string
  imap_port: number
  username: string
  use_ssl: boolean
  folders: string[]
  tags: string[]
  last_sync_at: string | null
  active: boolean
  created_at: string
  updated_at: string
}

export interface EmailAccountCreate {
  name: string
  email_address: string
  imap_server: string
  imap_port?: number
  username: string
  password: string
  use_ssl?: boolean
  folders?: string[]
  tags?: string[]
}

export interface EmailAccountUpdate {
  name?: string
  imap_server?: string
  imap_port?: number
  username?: string
  password?: string
  use_ssl?: boolean
  folders?: string[]
  tags?: string[]
  active?: boolean
}

// Types for Article Feeds
export interface ArticleFeed {
  id: number
  url: string
  title: string | null
  description: string | null
  tags: string[]
  check_interval: number
  last_checked_at: string | null
  active: boolean
  created_at: string
  updated_at: string
}

export interface ArticleFeedCreate {
  url: string
  title?: string
  description?: string
  tags?: string[]
  check_interval?: number
  active?: boolean
}

export interface ArticleFeedUpdate {
  title?: string
  description?: string
  tags?: string[]
  check_interval?: number
  active?: boolean
}

// Types for GitHub
export interface GithubRepo {
  id: number
  account_id: number
  owner: string
  name: string
  repo_path: string
  track_issues: boolean
  track_prs: boolean
  track_comments: boolean
  track_project_fields: boolean
  labels_filter: string[]
  state_filter: string | null
  tags: string[]
  check_interval: number
  full_sync_interval: number
  last_sync_at: string | null
  last_full_sync_at: string | null
  active: boolean
  created_at: string
}

export interface GithubAccount {
  id: number
  name: string
  auth_type: 'pat' | 'app'
  has_access_token: boolean
  has_private_key: boolean
  app_id: number | null
  installation_id: number | null
  active: boolean
  last_sync_at: string | null
  created_at: string
  updated_at: string
  repos: GithubRepo[]
}

export interface GithubAccountCreate {
  name: string
  auth_type: 'pat' | 'app'
  access_token?: string
  app_id?: number
  installation_id?: number
  private_key?: string
}

export interface GithubAccountUpdate {
  name?: string
  access_token?: string
  app_id?: number
  installation_id?: number
  private_key?: string
  active?: boolean
}

export interface GithubRepoCreate {
  owner: string
  name: string
  track_issues?: boolean
  track_prs?: boolean
  track_comments?: boolean
  track_project_fields?: boolean
  labels_filter?: string[]
  state_filter?: string
  tags?: string[]
  check_interval?: number
  full_sync_interval?: number
}

export interface GithubRepoUpdate {
  track_issues?: boolean
  track_prs?: boolean
  track_comments?: boolean
  track_project_fields?: boolean
  labels_filter?: string[]
  state_filter?: string
  tags?: string[]
  check_interval?: number
  full_sync_interval?: number
  active?: boolean
}

// Types for Google OAuth Config
export interface GoogleOAuthConfig {
  id: number
  name: string
  client_id: string
  project_id: string | null
  redirect_uris: string[]
  created_at: string
}

// Types for Google Drive
export interface GoogleFolder {
  id: number
  folder_id: string
  folder_name: string
  folder_path: string | null
  recursive: boolean
  include_shared: boolean
  tags: string[]
  check_interval: number
  last_sync_at: string | null
  active: boolean
  exclude_folder_ids: string[]
}

export interface GoogleAccount {
  id: number
  name: string
  email: string
  active: boolean
  last_sync_at: string | null
  sync_error: string | null
  folders: GoogleFolder[]
}

export interface GoogleFolderCreate {
  folder_id: string
  folder_name: string
  recursive?: boolean
  include_shared?: boolean
  tags?: string[]
  check_interval?: number
}

export interface GoogleFolderUpdate {
  folder_name?: string
  recursive?: boolean
  include_shared?: boolean
  tags?: string[]
  check_interval?: number
  active?: boolean
  exclude_folder_ids?: string[]
}

// Types for Google Drive browsing
export interface DriveItem {
  id: string
  name: string
  mime_type: string
  is_folder: boolean
  size: number | null
  modified_at: string | null
}

export interface BrowseResponse {
  folder_id: string
  folder_name: string
  parent_id: string | null
  items: DriveItem[]
  next_page_token: string | null
}

// Types for Calendar Accounts
export interface CalendarGoogleAccountInfo {
  id: number
  name: string
  email: string
}

export interface CalendarAccount {
  id: number
  name: string
  calendar_type: 'caldav' | 'google'
  caldav_url: string | null
  caldav_username: string | null
  google_account_id: number | null
  google_account: CalendarGoogleAccountInfo | null
  calendar_ids: string[]
  tags: string[]
  check_interval: number
  sync_past_days: number
  sync_future_days: number
  last_sync_at: string | null
  sync_error: string | null
  active: boolean
  created_at: string
  updated_at: string
}

export interface CalendarAccountCreate {
  name: string
  calendar_type: 'caldav' | 'google'
  caldav_url?: string
  caldav_username?: string
  caldav_password?: string
  google_account_id?: number
  calendar_ids?: string[]
  tags?: string[]
  check_interval?: number
  sync_past_days?: number
  sync_future_days?: number
}

export interface CalendarAccountUpdate {
  name?: string
  caldav_url?: string
  caldav_username?: string
  caldav_password?: string
  google_account_id?: number
  calendar_ids?: string[]
  tags?: string[]
  check_interval?: number
  sync_past_days?: number
  sync_future_days?: number
  active?: boolean
}

// Celery task response (for async jobs)
export interface CeleryTaskResponse {
  task_id: string
  status: string
}

// Todo/Task types
export interface TodoTask {
  id: number
  task_title: string
  due_date: string | null
  priority: 'low' | 'medium' | 'high' | 'urgent' | null
  status: 'pending' | 'in_progress' | 'done' | 'cancelled'
  recurrence: string | null
  completed_at: string | null
  source_item_id: number | null
  tags: string[]
  inserted_at: string | null
}

export interface TodoTaskCreate {
  task_title: string
  due_date?: string
  priority?: 'low' | 'medium' | 'high' | 'urgent'
  recurrence?: string
  tags?: string[]
}

export interface TodoTaskUpdate {
  task_title?: string
  due_date?: string
  priority?: 'low' | 'medium' | 'high' | 'urgent'
  status?: 'pending' | 'in_progress' | 'done' | 'cancelled'
  recurrence?: string
  tags?: string[]
}

export interface TodoTaskFilters {
  status?: string
  priority?: string
  include_completed?: boolean
  due_before?: string
  due_after?: string
  limit?: number
}

// Calendar Event
export interface CalendarEvent {
  id: number
  event_title: string
  start_time: string
  end_time: string | null
  all_day: boolean
  location: string | null
  calendar_name: string | null
  recurrence_rule: string | null
  calendar_account_id: number | null
}

export const useSources = () => {
  const { apiCall } = useAuth()

  // === Email Accounts ===

  const listEmailAccounts = useCallback(async (): Promise<EmailAccount[]> => {
    const response = await apiCall('/email-accounts')
    if (!response.ok) throw new Error('Failed to fetch email accounts')
    return response.json()
  }, [apiCall])

  const createEmailAccount = useCallback(async (data: EmailAccountCreate): Promise<EmailAccount> => {
    const response = await apiCall('/email-accounts', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to create email account')
    }
    return response.json()
  }, [apiCall])

  const updateEmailAccount = useCallback(async (id: number, data: EmailAccountUpdate): Promise<EmailAccount> => {
    const response = await apiCall(`/email-accounts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update email account')
    }
    return response.json()
  }, [apiCall])

  const deleteEmailAccount = useCallback(async (id: number): Promise<void> => {
    const response = await apiCall(`/email-accounts/${id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to delete email account')
  }, [apiCall])

  const syncEmailAccount = useCallback(async (id: number): Promise<CeleryTaskResponse> => {
    const response = await apiCall(`/email-accounts/${id}/sync`, { method: 'POST' })
    if (!response.ok) throw new Error('Failed to sync email account')
    return response.json()
  }, [apiCall])

  const testEmailAccount = useCallback(async (id: number): Promise<{ status: string; message: string }> => {
    const response = await apiCall(`/email-accounts/${id}/test`, { method: 'POST' })
    if (!response.ok) throw new Error('Failed to test email account')
    return response.json()
  }, [apiCall])

  // === Article Feeds ===

  const listArticleFeeds = useCallback(async (): Promise<ArticleFeed[]> => {
    const response = await apiCall('/article-feeds')
    if (!response.ok) throw new Error('Failed to fetch article feeds')
    return response.json()
  }, [apiCall])

  const createArticleFeed = useCallback(async (data: ArticleFeedCreate): Promise<ArticleFeed> => {
    const response = await apiCall('/article-feeds', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to create article feed')
    }
    return response.json()
  }, [apiCall])

  const updateArticleFeed = useCallback(async (id: number, data: ArticleFeedUpdate): Promise<ArticleFeed> => {
    const response = await apiCall(`/article-feeds/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update article feed')
    }
    return response.json()
  }, [apiCall])

  const deleteArticleFeed = useCallback(async (id: number): Promise<void> => {
    const response = await apiCall(`/article-feeds/${id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to delete article feed')
  }, [apiCall])

  const syncArticleFeed = useCallback(async (id: number): Promise<CeleryTaskResponse> => {
    const response = await apiCall(`/article-feeds/${id}/sync`, { method: 'POST' })
    if (!response.ok) throw new Error('Failed to sync article feed')
    return response.json()
  }, [apiCall])

  const discoverFeed = useCallback(async (url: string): Promise<{ url: string; title: string | null; description: string | null }> => {
    const response = await apiCall(`/article-feeds/discover?url=${encodeURIComponent(url)}`, {
      method: 'POST',
    })
    if (!response.ok) throw new Error('Failed to discover feed')
    return response.json()
  }, [apiCall])

  // === GitHub Accounts ===

  const listGithubAccounts = useCallback(async (): Promise<GithubAccount[]> => {
    const response = await apiCall('/github/accounts')
    if (!response.ok) throw new Error('Failed to fetch GitHub accounts')
    return response.json()
  }, [apiCall])

  const createGithubAccount = useCallback(async (data: GithubAccountCreate): Promise<GithubAccount> => {
    const response = await apiCall('/github/accounts', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to create GitHub account')
    }
    return response.json()
  }, [apiCall])

  const updateGithubAccount = useCallback(async (id: number, data: GithubAccountUpdate): Promise<GithubAccount> => {
    const response = await apiCall(`/github/accounts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update GitHub account')
    }
    return response.json()
  }, [apiCall])

  const deleteGithubAccount = useCallback(async (id: number): Promise<void> => {
    const response = await apiCall(`/github/accounts/${id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to delete GitHub account')
  }, [apiCall])

  const validateGithubAccount = useCallback(async (id: number): Promise<{ status: string; message: string }> => {
    const response = await apiCall(`/github/accounts/${id}/validate`, { method: 'POST' })
    if (!response.ok) throw new Error('Failed to validate GitHub account')
    return response.json()
  }, [apiCall])

  // === GitHub Repos ===

  const addGithubRepo = useCallback(async (accountId: number, data: GithubRepoCreate): Promise<GithubRepo> => {
    const response = await apiCall(`/github/accounts/${accountId}/repos`, {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to add GitHub repo')
    }
    return response.json()
  }, [apiCall])

  const updateGithubRepo = useCallback(async (accountId: number, repoId: number, data: GithubRepoUpdate): Promise<GithubRepo> => {
    const response = await apiCall(`/github/accounts/${accountId}/repos/${repoId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update GitHub repo')
    }
    return response.json()
  }, [apiCall])

  const deleteGithubRepo = useCallback(async (accountId: number, repoId: number): Promise<void> => {
    const response = await apiCall(`/github/accounts/${accountId}/repos/${repoId}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to delete GitHub repo')
  }, [apiCall])

  const syncGithubRepo = useCallback(async (accountId: number, repoId: number, forceFull = false): Promise<CeleryTaskResponse> => {
    const response = await apiCall(`/github/accounts/${accountId}/repos/${repoId}/sync?force_full=${forceFull}`, { method: 'POST' })
    if (!response.ok) throw new Error('Failed to sync GitHub repo')
    return response.json()
  }, [apiCall])

  // === Google Drive ===

  const listGoogleAccounts = useCallback(async (): Promise<GoogleAccount[]> => {
    const response = await apiCall('/google-drive/accounts')
    if (!response.ok) throw new Error('Failed to fetch Google accounts')
    return response.json()
  }, [apiCall])

  const getGoogleAuthUrl = useCallback(async (): Promise<{ authorization_url: string }> => {
    const response = await apiCall('/google-drive/authorize')
    if (!response.ok) throw new Error('Failed to get Google auth URL')
    return response.json()
  }, [apiCall])

  const deleteGoogleAccount = useCallback(async (id: number): Promise<void> => {
    const response = await apiCall(`/google-drive/accounts/${id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to delete Google account')
  }, [apiCall])

  const browseGoogleDrive = useCallback(async (
    accountId: number,
    folderId: string = 'root',
    pageToken?: string
  ): Promise<BrowseResponse> => {
    const params = new URLSearchParams({ folder_id: folderId })
    if (pageToken) params.append('page_token', pageToken)
    const response = await apiCall(`/google-drive/accounts/${accountId}/browse?${params}`)
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to browse Google Drive')
    }
    return response.json()
  }, [apiCall])

  const addGoogleFolder = useCallback(async (accountId: number, data: GoogleFolderCreate): Promise<GoogleFolder> => {
    const response = await apiCall(`/google-drive/accounts/${accountId}/folders`, {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to add Google folder')
    }
    return response.json()
  }, [apiCall])

  const updateGoogleFolder = useCallback(async (accountId: number, folderId: number, data: GoogleFolderUpdate): Promise<GoogleFolder> => {
    const response = await apiCall(`/google-drive/accounts/${accountId}/folders/${folderId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update Google folder')
    }
    return response.json()
  }, [apiCall])

  const deleteGoogleFolder = useCallback(async (accountId: number, folderId: number): Promise<void> => {
    const response = await apiCall(`/google-drive/accounts/${accountId}/folders/${folderId}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to delete Google folder')
  }, [apiCall])

  const syncGoogleFolder = useCallback(async (accountId: number, folderId: number, forceFull = false): Promise<CeleryTaskResponse> => {
    const response = await apiCall(`/google-drive/accounts/${accountId}/folders/${folderId}/sync?force_full=${forceFull}`, { method: 'POST' })
    if (!response.ok) throw new Error('Failed to sync Google folder')
    return response.json()
  }, [apiCall])

  // === Google OAuth Config ===

  const getGoogleOAuthConfig = useCallback(async (): Promise<GoogleOAuthConfig | null> => {
    const response = await apiCall('/google-drive/config')
    if (!response.ok) {
      if (response.status === 404) return null
      throw new Error('Failed to fetch Google OAuth config')
    }
    return response.json()
  }, [apiCall])

  const uploadGoogleOAuthConfig = useCallback(async (file: File): Promise<GoogleOAuthConfig> => {
    const formData = new FormData()
    formData.append('file', file)

    const accessToken = document.cookie
      .split('; ')
      .find(row => row.startsWith('access_token='))
      ?.split('=')[1]

    const response = await fetch('/google-drive/config', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${accessToken}`,
      },
      body: formData,
    })

    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to upload OAuth config')
    }
    return response.json()
  }, [])

  const deleteGoogleOAuthConfig = useCallback(async (): Promise<void> => {
    const response = await apiCall('/google-drive/config', { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to delete Google OAuth config')
  }, [apiCall])

  // === Calendar Accounts ===

  const listCalendarAccounts = useCallback(async (): Promise<CalendarAccount[]> => {
    const response = await apiCall('/calendar-accounts')
    if (!response.ok) throw new Error('Failed to fetch calendar accounts')
    return response.json()
  }, [apiCall])

  const createCalendarAccount = useCallback(async (data: CalendarAccountCreate): Promise<CalendarAccount> => {
    const response = await apiCall('/calendar-accounts', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to create calendar account')
    }
    return response.json()
  }, [apiCall])

  const updateCalendarAccount = useCallback(async (id: number, data: CalendarAccountUpdate): Promise<CalendarAccount> => {
    const response = await apiCall(`/calendar-accounts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update calendar account')
    }
    return response.json()
  }, [apiCall])

  const deleteCalendarAccount = useCallback(async (id: number): Promise<void> => {
    const response = await apiCall(`/calendar-accounts/${id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to delete calendar account')
  }, [apiCall])

  const syncCalendarAccount = useCallback(async (id: number, forceFull = false): Promise<CeleryTaskResponse> => {
    const response = await apiCall(`/calendar-accounts/${id}/sync?force_full=${forceFull}`, { method: 'POST' })
    if (!response.ok) throw new Error('Failed to sync calendar account')
    return response.json()
  }, [apiCall])

  const getUpcomingEvents = useCallback(async (
    options: { days?: number; limit?: number; startDate?: string; endDate?: string } = {}
  ): Promise<CalendarEvent[]> => {
    const { days = 7, limit = 100, startDate, endDate } = options
    let url = `/calendar-accounts/events/upcoming?limit=${limit}`
    if (startDate && endDate) {
      url += `&start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`
    } else {
      url += `&days=${days}`
    }
    const response = await apiCall(url)
    if (!response.ok) throw new Error('Failed to fetch upcoming events')
    return response.json()
  }, [apiCall])

  // === Tasks/Todos ===

  const listTasks = useCallback(async (filters: TodoTaskFilters = {}): Promise<TodoTask[]> => {
    const params = new URLSearchParams()
    if (filters.status) params.append('status', filters.status)
    if (filters.priority) params.append('priority', filters.priority)
    if (filters.include_completed) params.append('include_completed', 'true')
    if (filters.due_before) params.append('due_before', filters.due_before)
    if (filters.due_after) params.append('due_after', filters.due_after)
    if (filters.limit) params.append('limit', filters.limit.toString())

    const queryString = params.toString()
    const url = queryString ? `/tasks?${queryString}` : '/tasks'
    const response = await apiCall(url)
    if (!response.ok) throw new Error('Failed to fetch tasks')
    return response.json()
  }, [apiCall])

  const createTask = useCallback(async (data: TodoTaskCreate): Promise<TodoTask> => {
    const response = await apiCall('/tasks', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to create task')
    }
    return response.json()
  }, [apiCall])

  const updateTask = useCallback(async (id: number, data: TodoTaskUpdate): Promise<TodoTask> => {
    const response = await apiCall(`/tasks/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update task')
    }
    return response.json()
  }, [apiCall])

  const deleteTask = useCallback(async (id: number): Promise<void> => {
    const response = await apiCall(`/tasks/${id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to delete task')
  }, [apiCall])

  const completeTask = useCallback(async (id: number): Promise<TodoTask> => {
    const response = await apiCall(`/tasks/${id}/complete`, { method: 'POST' })
    if (!response.ok) throw new Error('Failed to complete task')
    return response.json()
  }, [apiCall])

  return {
    // Email
    listEmailAccounts,
    createEmailAccount,
    updateEmailAccount,
    deleteEmailAccount,
    syncEmailAccount,
    testEmailAccount,
    // Article Feeds
    listArticleFeeds,
    createArticleFeed,
    updateArticleFeed,
    deleteArticleFeed,
    syncArticleFeed,
    discoverFeed,
    // GitHub Accounts
    listGithubAccounts,
    createGithubAccount,
    updateGithubAccount,
    deleteGithubAccount,
    validateGithubAccount,
    // GitHub Repos
    addGithubRepo,
    updateGithubRepo,
    deleteGithubRepo,
    syncGithubRepo,
    // Google Drive
    listGoogleAccounts,
    getGoogleAuthUrl,
    deleteGoogleAccount,
    browseGoogleDrive,
    addGoogleFolder,
    updateGoogleFolder,
    deleteGoogleFolder,
    syncGoogleFolder,
    // Google OAuth Config
    getGoogleOAuthConfig,
    uploadGoogleOAuthConfig,
    deleteGoogleOAuthConfig,
    // Calendar Accounts
    listCalendarAccounts,
    createCalendarAccount,
    updateCalendarAccount,
    deleteCalendarAccount,
    syncCalendarAccount,
    // Calendar Events
    getUpcomingEvents,
    // Tasks/Todos
    listTasks,
    createTask,
    updateTask,
    deleteTask,
    completeTask,
  }
}
