import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useUsers, type User, type UserCreate, type UserUpdate } from '../../hooks/useUsers'
import { useAuth } from '../../hooks/useAuth'

// Common scopes that can be assigned to users
const AVAILABLE_SCOPES = [
  { value: 'read', label: 'Read', description: 'View knowledge base content' },
  { value: 'observe', label: 'Observe', description: 'Record observations' },
  { value: 'github', label: 'GitHub', description: 'GitHub integration access' },
  { value: 'email', label: 'Email', description: 'Email integration access' },
  { value: 'admin:users', label: 'User Admin', description: 'Manage users' },
  { value: '*', label: 'Full Access', description: 'Access to all features' },
]

interface UserFormData {
  name: string
  email: string
  password: string
  user_type: 'human' | 'bot'
  scopes: string[]
}

const UserManagement = () => {
  const { listUsers, createUser, updateUser, deleteUser, regenerateApiKey } = useUsers()
  const { user: currentUser } = useAuth()

  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Create modal state
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [createForm, setCreateForm] = useState<UserFormData>({
    name: '',
    email: '',
    password: '',
    user_type: 'human',
    scopes: ['read'],
  })
  const [createLoading, setCreateLoading] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  // Edit modal state
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [editForm, setEditForm] = useState<UserUpdate>({})
  const [editLoading, setEditLoading] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  // Delete confirmation
  const [deletingUser, setDeletingUser] = useState<User | null>(null)
  const [deleteLoading, setDeleteLoading] = useState(false)

  // API Key regeneration
  const [apiKeyUser, setApiKeyUser] = useState<User | null>(null)
  const [newApiKey, setNewApiKey] = useState<string | null>(null)
  const [apiKeyLoading, setApiKeyLoading] = useState(false)

  const loadUsers = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listUsers()
      setUsers(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }, [listUsers])

  useEffect(() => {
    loadUsers()
  }, [loadUsers])

  const handleCreate = async () => {
    setCreateLoading(true)
    setCreateError(null)

    try {
      const data: UserCreate = {
        name: createForm.name,
        email: createForm.email,
        user_type: createForm.user_type,
        scopes: createForm.scopes,
      }

      if (createForm.user_type === 'human') {
        if (!createForm.password) {
          setCreateError('Password is required for human users')
          setCreateLoading(false)
          return
        }
        data.password = createForm.password
      }

      await createUser(data)
      setShowCreateModal(false)
      setCreateForm({ name: '', email: '', password: '', user_type: 'human', scopes: ['read'] })
      await loadUsers()
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : 'Failed to create user')
    } finally {
      setCreateLoading(false)
    }
  }

  const handleEdit = async () => {
    if (!editingUser) return

    setEditLoading(true)
    setEditError(null)

    try {
      await updateUser(editingUser.id, editForm)
      setEditingUser(null)
      setEditForm({})
      await loadUsers()
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Failed to update user')
    } finally {
      setEditLoading(false)
    }
  }

  const handleDelete = async () => {
    if (!deletingUser) return

    setDeleteLoading(true)

    try {
      await deleteUser(deletingUser.id)
      setDeletingUser(null)
      await loadUsers()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete user')
    } finally {
      setDeleteLoading(false)
    }
  }

  const handleRegenerateApiKey = async () => {
    if (!apiKeyUser) return

    setApiKeyLoading(true)

    try {
      const result = await regenerateApiKey(apiKeyUser.id)
      setNewApiKey(result.api_key)
      await loadUsers()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to regenerate API key')
      setApiKeyUser(null)
    } finally {
      setApiKeyLoading(false)
    }
  }

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
    } catch (e) {
      console.error('Failed to copy:', e)
    }
  }

  const toggleScope = (scopes: string[], scope: string): string[] => {
    if (scopes.includes(scope)) {
      return scopes.filter((s) => s !== scope)
    }
    return [...scopes, scope]
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 p-8 flex items-center justify-center">
        <p className="text-slate-500">Loading...</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50 p-8">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <header className="flex items-center gap-4 mb-8 pb-4 border-b border-slate-200">
          <Link
            to="/ui/dashboard"
            className="bg-slate-100 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-200 transition-colors"
          >
            &larr; Back
          </Link>
          <h1 className="text-2xl font-semibold text-slate-800 flex-1">User Management</h1>
          <button
            onClick={() => setShowCreateModal(true)}
            className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Add User
          </button>
        </header>

        {/* Error message */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 text-red-700 rounded-lg">
            {error}
            <button onClick={() => setError(null)} className="ml-4 underline">
              Dismiss
            </button>
          </div>
        )}

        {/* User list */}
        <div className="space-y-4">
          {users.length === 0 ? (
            <div className="bg-white rounded-xl shadow-md p-8 text-center text-slate-500">
              No users found. Create your first user above.
            </div>
          ) : (
            users.map((user) => (
              <div key={user.id} className="bg-white rounded-xl shadow-md p-6">
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-4">
                    {/* User type badge */}
                    <div
                      className={`px-2 py-1 rounded text-xs font-medium ${
                        user.user_type === 'human'
                          ? 'bg-blue-100 text-blue-700'
                          : 'bg-purple-100 text-purple-700'
                      }`}
                    >
                      {user.user_type === 'human' ? 'Human' : 'Bot'}
                    </div>

                    <div>
                      <h3 className="font-semibold text-slate-800">
                        {user.name}
                        {user.id === currentUser?.id && (
                          <span className="ml-2 text-xs text-slate-500">(you)</span>
                        )}
                      </h3>
                      <p className="text-sm text-slate-500">{user.email}</p>

                      {/* Scopes */}
                      <div className="flex flex-wrap gap-1 mt-2">
                        {user.scopes.map((scope) => (
                          <span
                            key={scope}
                            className="bg-slate-100 text-slate-600 px-2 py-0.5 rounded text-xs"
                          >
                            {scope}
                          </span>
                        ))}
                      </div>

                      {/* API Key status */}
                      {user.has_api_key && (
                        <p className="text-xs text-slate-400 mt-2">Has API key configured</p>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2">
                    {user.user_type === 'bot' && (
                      <button
                        onClick={() => setApiKeyUser(user)}
                        className="text-sm text-slate-600 hover:text-slate-800 px-3 py-1 rounded hover:bg-slate-100 transition-colors"
                      >
                        {user.has_api_key ? 'Regenerate Key' : 'Generate Key'}
                      </button>
                    )}
                    <button
                      onClick={() => {
                        setEditingUser(user)
                        setEditForm({
                          name: user.name,
                          email: user.email,
                          scopes: [...user.scopes],
                        })
                      }}
                      className="text-sm text-primary hover:text-primary/80 px-3 py-1 rounded hover:bg-primary/10 transition-colors"
                    >
                      Edit
                    </button>
                    {user.id !== currentUser?.id && (
                      <button
                        onClick={() => setDeletingUser(user)}
                        className="text-sm text-red-600 hover:text-red-700 px-3 py-1 rounded hover:bg-red-50 transition-colors"
                      >
                        Delete
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Create User Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md m-4 max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">Create New User</h3>

            {createError && (
              <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg text-sm">{createError}</div>
            )}

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">User Type</label>
                <select
                  value={createForm.user_type}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, user_type: e.target.value as 'human' | 'bot' })
                  }
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                >
                  <option value="human">Human</option>
                  <option value="bot">Bot</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Name</label>
                <input
                  type="text"
                  value={createForm.name}
                  onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Email</label>
                <input
                  type="email"
                  value={createForm.email}
                  onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>

              {createForm.user_type === 'human' && (
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1">Password</label>
                  <input
                    type="password"
                    value={createForm.password}
                    onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                    className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                  />
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-2">Scopes</label>
                <div className="space-y-2">
                  {AVAILABLE_SCOPES.map((scope) => (
                    <label key={scope.value} className="flex items-start gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={createForm.scopes.includes(scope.value)}
                        onChange={() =>
                          setCreateForm({
                            ...createForm,
                            scopes: toggleScope(createForm.scopes, scope.value),
                          })
                        }
                        className="mt-0.5"
                      />
                      <div>
                        <span className="text-sm font-medium text-slate-700">{scope.label}</span>
                        <p className="text-xs text-slate-500">{scope.description}</p>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => {
                  setShowCreateModal(false)
                  setCreateForm({ name: '', email: '', password: '', user_type: 'human', scopes: ['read'] })
                  setCreateError(null)
                }}
                className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={createLoading}
                className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 disabled:bg-slate-400 transition-colors"
              >
                {createLoading ? 'Creating...' : 'Create User'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit User Modal */}
      {editingUser && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md m-4 max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">Edit User</h3>

            {editError && (
              <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg text-sm">{editError}</div>
            )}

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Name</label>
                <input
                  type="text"
                  value={editForm.name || ''}
                  onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Email</label>
                <input
                  type="email"
                  value={editForm.email || ''}
                  onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-2">Scopes</label>
                <div className="space-y-2">
                  {AVAILABLE_SCOPES.map((scope) => (
                    <label key={scope.value} className="flex items-start gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={(editForm.scopes || []).includes(scope.value)}
                        onChange={() =>
                          setEditForm({
                            ...editForm,
                            scopes: toggleScope(editForm.scopes || [], scope.value),
                          })
                        }
                        className="mt-0.5"
                      />
                      <div>
                        <span className="text-sm font-medium text-slate-700">{scope.label}</span>
                        <p className="text-xs text-slate-500">{scope.description}</p>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => {
                  setEditingUser(null)
                  setEditForm({})
                  setEditError(null)
                }}
                className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleEdit}
                disabled={editLoading}
                className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 disabled:bg-slate-400 transition-colors"
              >
                {editLoading ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deletingUser && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md m-4">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">Delete User</h3>

            <p className="text-sm text-slate-600 mb-4">
              Are you sure you want to delete <strong>{deletingUser.name}</strong>? This action cannot
              be undone.
            </p>

            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeletingUser(null)}
                className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={deleteLoading}
                className="bg-red-600 text-white py-2 px-4 rounded-lg hover:bg-red-700 disabled:bg-slate-400 transition-colors"
              >
                {deleteLoading ? 'Deleting...' : 'Delete User'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* API Key Modal */}
      {apiKeyUser && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md m-4">
            {!newApiKey ? (
              <>
                <h3 className="text-lg font-semibold text-slate-800 mb-4">
                  {apiKeyUser.has_api_key ? 'Regenerate' : 'Generate'} API Key
                </h3>

                <p className="text-sm text-slate-600 mb-4">
                  {apiKeyUser.has_api_key
                    ? `Are you sure you want to regenerate the API key for ${apiKeyUser.name}? The current key will be invalidated.`
                    : `Generate an API key for ${apiKeyUser.name}.`}
                </p>

                <div className="flex justify-end gap-3">
                  <button
                    onClick={() => setApiKeyUser(null)}
                    className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleRegenerateApiKey}
                    disabled={apiKeyLoading}
                    className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 disabled:bg-slate-400 transition-colors"
                  >
                    {apiKeyLoading ? 'Generating...' : apiKeyUser.has_api_key ? 'Regenerate' : 'Generate'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3 className="text-lg font-semibold text-slate-800 mb-4">New API Key Generated</h3>

                <p className="text-sm text-slate-600 mb-4">
                  Copy the API key now. It won't be shown again.
                </p>

                <div className="bg-slate-100 p-3 rounded-lg mb-4">
                  <code className="text-sm text-slate-800 break-all">{newApiKey}</code>
                </div>

                <div className="flex justify-end gap-3">
                  <button
                    onClick={() => copyToClipboard(newApiKey)}
                    className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
                  >
                    Copy
                  </button>
                  <button
                    onClick={() => {
                      setApiKeyUser(null)
                      setNewApiKey(null)
                    }}
                    className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 transition-colors"
                  >
                    Done
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default UserManagement
