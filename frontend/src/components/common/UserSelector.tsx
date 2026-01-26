import { useState, useEffect, useMemo } from 'react'
import { useAuth } from '../../hooks/useAuth'
import { useUsers, User } from '../../hooks/useUsers'

export type SelectedUser = { type: 'user'; id: number; name: string }

// Minimal user info for filtering
export interface FilterUser {
  id: number
  name: string
}

interface UserSelectorProps {
  value: SelectedUser
  onChange: (user: SelectedUser) => void
  className?: string
  // Optional: only show users that exist in this list (e.g., users with telemetry data)
  filterToUsers?: FilterUser[]
  // Only show human users (excludes bots)
  onlyHumanUsers?: boolean
}

/**
 * User selector dropdown for admin users.
 * Allows admins to select a specific user to view their data.
 * Non-admin users will not see this component (returns null).
 *
 * If filterToUsers is provided, only users in that list will be shown.
 * If onlyHumanUsers is true, bot users will be excluded.
 */
const UserSelector = ({ value, onChange, className = '', filterToUsers, onlyHumanUsers = false }: UserSelectorProps) => {
  const { hasScope, user: currentUser } = useAuth()
  const { listUsers } = useUsers()
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const isAdmin = hasScope('admin') || hasScope('*')

  useEffect(() => {
    if (!isAdmin) return

    const loadUsers = async () => {
      try {
        setLoading(true)
        const userList = await listUsers()
        setUsers(userList)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load users')
      } finally {
        setLoading(false)
      }
    }

    loadUsers()
  }, [isAdmin, listUsers])

  // Filter users based on props
  const displayUsers = useMemo(() => {
    let filtered = users
    // Filter to only human users if requested
    if (onlyHumanUsers) {
      filtered = filtered.filter(u => u.user_type === 'human')
    }
    // Filter to specific users if provided
    if (filterToUsers) {
      const filterIds = new Set(filterToUsers.map(u => u.id))
      filtered = filtered.filter(u => filterIds.has(u.id))
    }
    return filtered
  }, [users, filterToUsers, onlyHumanUsers])

  // Don't render for non-admins
  if (!isAdmin) return null

  if (loading) {
    return (
      <div className={`flex items-center gap-2 ${className}`}>
        <span className="text-sm text-slate-500">Loading users...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className={`flex items-center gap-2 ${className}`}>
        <span className="text-sm text-red-500">{error}</span>
      </div>
    )
  }

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <label htmlFor="user-selector" className="text-sm text-slate-600 font-medium">
        View as:
      </label>
      <select
        id="user-selector"
        value={value.id}
        onChange={(e) => {
          const userId = parseInt(e.target.value, 10)
          const user = users.find(u => u.id === userId)
          onChange({ type: 'user', id: userId, name: user?.name || '' })
        }}
        className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 bg-white text-slate-700 hover:border-slate-300 focus:border-primary focus:ring-1 focus:ring-primary outline-none"
      >
        {displayUsers.map(user => (
          <option key={user.id} value={user.id}>
            {user.name} {user.id === currentUser?.id ? '(you)' : ''}
          </option>
        ))}
      </select>
    </div>
  )
}

export default UserSelector

/**
 * Hook to manage user selection state with localStorage persistence.
 * Returns [selectedUser, setSelectedUser] similar to useState.
 */
export function useUserSelection(storageKey: string = 'adminSelectedUser'): [SelectedUser, (user: SelectedUser) => void] {
  const { hasScope, user: currentUser } = useAuth()
  const isAdmin = hasScope('admin') || hasScope('*')

  const [selectedUser, setSelectedUserState] = useState<SelectedUser>(() => {
    // For admins, try to restore from localStorage
    if (isAdmin) {
      try {
        const stored = localStorage.getItem(storageKey)
        if (stored) {
          const parsed = JSON.parse(stored)
          // Migrate old 'all' type to current user
          if (parsed.type === 'user' && parsed.id) {
            return parsed
          }
        }
      } catch {
        // Ignore parsing errors
      }
    }

    // Default to current user for everyone
    if (currentUser) {
      return { type: 'user', id: currentUser.id, name: currentUser.name }
    }
    // Temporary state until currentUser loads - the useEffect will fix this
    return { type: 'user', id: 0, name: '' }
  })

  const setSelectedUser = (user: SelectedUser) => {
    setSelectedUserState(user)
    if (isAdmin) {
      try {
        localStorage.setItem(storageKey, JSON.stringify(user))
      } catch {
        // Ignore storage errors
      }
    }
  }

  // Update if currentUser changes and selected user is placeholder (id: 0)
  useEffect(() => {
    if (currentUser && selectedUser.id === 0) {
      setSelectedUserState({ type: 'user', id: currentUser.id, name: currentUser.name })
    }
  }, [currentUser, selectedUser.id])

  return [selectedUser, setSelectedUser]
}
