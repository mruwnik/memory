import { useState, useEffect, useCallback } from 'react'
import {
  useUsers,
  type User,
  type UserCreate,
  type UserUpdate,
  type ScopeInfo,
} from '../../hooks/useUsers'
import { useAuth } from '../../hooks/useAuth'
import { Button, Card, Input, Label, Modal, Notice, Page, Select } from '@/components/shared/ui'

interface UserFormData {
  name: string
  email: string
  password: string
  user_type: 'human' | 'bot'
  scopes: string[]
}

const UserManagement = () => {
  const { listUsers, listScopes, createUser, updateUser, deleteUser, regenerateApiKey } =
    useUsers()
  const { user: currentUser } = useAuth()

  const [users, setUsers] = useState<User[]>([])
  const [availableScopes, setAvailableScopes] = useState<ScopeInfo[]>([])
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

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [usersData, scopesData] = await Promise.all([listUsers(), listScopes()])
      setUsers(usersData)
      setAvailableScopes(scopesData)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [listUsers, listScopes])

  const loadUsers = useCallback(async () => {
    try {
      const data = await listUsers()
      setUsers(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load users')
    }
  }, [listUsers])

  useEffect(() => {
    loadData()
  }, [loadData])

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
      setNewApiKey(result.key)
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
    return <Page title="User Management" width="narrow" loading />
  }

  return (
    <>
      <Page
        title="User Management"
        actions={<>
          <Button variant="primary"
            onClick={() => setShowCreateModal(true)}

          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Add User
          </Button>
        </>}
        width="narrow"
      >

        {/* Error message */}
        {error && (
          <Notice tone="error" onDismiss={() => setError(null)}>{error}</Notice>
        )}

        {/* User list */}
        <div className="space-y-4">
          {users.length === 0 ? (
            <Card className="text-center text-slate-500">
              No users found. Create your first user above.
            </Card>
          ) : (
            users.map((user) => (
              <Card key={user.id}>
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
                      {user.api_key_count > 0 && (
                        <p className="text-xs text-slate-400 mt-2">Has API key configured</p>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2">
                    {user.user_type === 'bot' && (
                      <Button size="sm"
                        onClick={() => setApiKeyUser(user)}
                      >
                        {user.api_key_count > 0 ? 'Regenerate Key' : 'Generate Key'}
                      </Button>
                    )}
                    <Button size="sm"
                      onClick={() => {
                        setEditingUser(user)
                        setEditForm({
                          name: user.name,
                          email: user.email,
                          scopes: [...user.scopes],
                        })
                      }}
                    >
                      Edit
                    </Button>
                    {user.id !== currentUser?.id && (
                      <Button variant="danger" size="sm"
                        onClick={() => setDeletingUser(user)}
                      >
                        Delete
                      </Button>
                    )}
                  </div>
                </div>
              </Card>
            ))
          )}
        </div>
      </Page>

      {/* Create User Modal */}
      {showCreateModal && (
        <Modal title="Create New User" onClose={() => {
                  setShowCreateModal(false)
                  setCreateForm({ name: '', email: '', password: '', user_type: 'human', scopes: ['read'] })
                  setCreateError(null)
                }} size="md">
          {createError && (
              <Notice tone="error" className="mb-4">{createError}</Notice>
            )}

            <div className="space-y-4">
              <div>
                <Label className="mb-1">User Type</Label>
                <Select
                  value={createForm.user_type}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, user_type: e.target.value as 'human' | 'bot' })
                  }
                >
                  <option value="human">Human</option>
                  <option value="bot">Bot</option>
                </Select>
              </div>

              <div>
                <Label className="mb-1">Name</Label>
                <Input
                  type="text"
                  value={createForm.name}
                  onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                />
              </div>

              <div>
                <Label className="mb-1">Email</Label>
                <Input
                  type="email"
                  value={createForm.email}
                  onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                />
              </div>

              {createForm.user_type === 'human' && (
                <div>
                  <Label className="mb-1">Password</Label>
                  <Input
                    type="password"
                    value={createForm.password}
                    onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                  />
                </div>
              )}

              <div>
                <Label className="mb-2">Scopes</Label>
                <div className="space-y-2">
                  {availableScopes.map((scope) => (
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
              <Button
                onClick={() => {
                  setShowCreateModal(false)
                  setCreateForm({ name: '', email: '', password: '', user_type: 'human', scopes: ['read'] })
                  setCreateError(null)
                }}
              >
                Cancel
              </Button>
              <Button variant="primary"
                onClick={handleCreate}
                disabled={createLoading}
              >
                {createLoading ? 'Creating...' : 'Create User'}
              </Button>
            </div>
        </Modal>
      )}

      {/* Edit User Modal */}
      {editingUser && (
        <Modal title="Edit User" onClose={() => {
                  setEditingUser(null)
                  setEditForm({})
                  setEditError(null)
                }} size="md">
          {editError && (
              <Notice tone="error" className="mb-4">{editError}</Notice>
            )}

            <div className="space-y-4">
              <div>
                <Label className="mb-1">Name</Label>
                <Input
                  type="text"
                  value={editForm.name || ''}
                  onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                />
              </div>

              <div>
                <Label className="mb-1">Email</Label>
                <Input
                  type="email"
                  value={editForm.email || ''}
                  onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                />
              </div>

              <div>
                <Label className="mb-2">Scopes</Label>
                <div className="space-y-2">
                  {availableScopes.map((scope) => (
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
              <Button
                onClick={() => {
                  setEditingUser(null)
                  setEditForm({})
                  setEditError(null)
                }}
              >
                Cancel
              </Button>
              <Button variant="primary"
                onClick={handleEdit}
                disabled={editLoading}
              >
                {editLoading ? 'Saving...' : 'Save Changes'}
              </Button>
            </div>
        </Modal>
      )}

      {/* Delete Confirmation Modal */}
      {deletingUser && (
        <Modal title="Delete User" onClose={() => setDeletingUser(null)} size="md">
          <p className="text-sm text-slate-600 mb-4">
              Are you sure you want to delete <strong>{deletingUser.name}</strong>? This action cannot
              be undone.
            </p>

            <div className="flex justify-end gap-3">
              <Button
                onClick={() => setDeletingUser(null)}
              >
                Cancel
              </Button>
              <Button variant="dangerSolid"
                onClick={handleDelete}
                disabled={deleteLoading}
              >
                {deleteLoading ? 'Deleting...' : 'Delete User'}
              </Button>
            </div>
        </Modal>
      )}

      {/* API Key Modal */}
      {apiKeyUser && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md m-4">
            {!newApiKey ? (
              <>
                <h3 className="text-lg font-semibold text-slate-800 mb-4">
                  {apiKeyUser.api_key_count > 0 ? 'Regenerate' : 'Generate'} API Key
                </h3>

                <p className="text-sm text-slate-600 mb-4">
                  {apiKeyUser.api_key_count > 0
                    ? `Are you sure you want to regenerate the API key for ${apiKeyUser.name}? The current key will be invalidated.`
                    : `Generate an API key for ${apiKeyUser.name}.`}
                </p>

                <div className="flex justify-end gap-3">
                  <Button
                    onClick={() => setApiKeyUser(null)}
                  >
                    Cancel
                  </Button>
                  <Button variant="primary"
                    onClick={handleRegenerateApiKey}
                    disabled={apiKeyLoading}
                  >
                    {apiKeyLoading ? 'Generating...' : apiKeyUser.api_key_count > 0 ? 'Regenerate' : 'Generate'}
                  </Button>
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
                  <Button
                    onClick={() => copyToClipboard(newApiKey)}
                  >
                    Copy
                  </Button>
                  <Button variant="primary"
                    onClick={() => {
                      setApiKeyUser(null)
                      setNewApiKey(null)
                    }}
                  >
                    Done
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}

export default UserManagement
