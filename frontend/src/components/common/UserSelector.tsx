import { useState, useEffect } from 'react'
import { useAuth } from '../../hooks/useAuth'
import { useUsers, User } from '../../hooks/useUsers'

export type SelectedUser = { type: 'all' } | { type: 'user'; id: number; name: string }

interface UserSelectorProps {
  value: SelectedUser
  onChange: (user: SelectedUser) => void
  className?: string
}

/**
 * User selector dropdown for admin users.
 * Allows admins to select a specific user or "All Users" to view aggregate data.
 * Non-admin users will not see this component (returns null).
 */
const UserSelector = ({ value, onChange, className = '' }: UserSelectorProps) => {
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

  const displayValue = value.type === 'all'
    ? 'All Users'
    : value.name || `User #${value.id}`

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <label htmlFor="user-selector" className="text-sm text-slate-600 font-medium">
        View as:
      </label>
      <select
        id="user-selector"
        value={value.type === 'all' ? 'all' : `user:${value.id}`}
        onChange={(e) => {
          const val = e.target.value
          if (val === 'all') {
            onChange({ type: 'all' })
          } else {
            const userId = parseInt(val.replace('user:', ''), 10)
            const user = users.find(u => u.id === userId)
            onChange({ type: 'user', id: userId, name: user?.name || '' })
          }
        }}
        className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 bg-white text-slate-700 hover:border-slate-300 focus:border-primary focus:ring-1 focus:ring-primary outline-none"
      >
        <option value="all">All Users</option>
        {users.map(user => (
          <option key={user.id} value={`user:${user.id}`}>
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
          return JSON.parse(stored)
        }
      } catch {
        // Ignore parsing errors
      }
      // Default to "all" for admins
      return { type: 'all' }
    }

    // Non-admins always see their own data
    // If currentUser isn't loaded yet, use a placeholder that will be updated
    // by the useEffect below once the user data is available
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

  // Update if currentUser changes and we're not admin
  useEffect(() => {
    if (!isAdmin && currentUser) {
      setSelectedUserState({ type: 'user', id: currentUser.id, name: currentUser.name })
    }
  }, [isAdmin, currentUser])

  return [selectedUser, setSelectedUser]
}
