import { useState, useEffect, useCallback, useId, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  useUsers,
  type User,
  type UserCreate,
  type UserUpdate,
  type ScopeInfo,
} from '../../hooks/useUsers'
import { useAuth } from '../../hooks/useAuth'
import { ApiError } from '@/hooks/apiError'
import { useModule } from '@/hooks/useModule'
import { Badge, Button, Checkbox, ConfirmButton, EmptyState, Field, Fieldset, FormActions, Hint, Input, List, Modal, Notice, Page, Row, Select } from '@/components/shared/ui'
import { NewApiKey } from './NewApiKey'
import { NewPasswordFields, passwordIsValid } from './NewPasswordFields'

const toggleScope = (scopes: string[], scope: string): string[] =>
  scopes.includes(scope) ? scopes.filter((s) => s !== scope) : [...scopes, scope]

const ScopeFieldset = ({ scopes, value, onChange, locked = [] }: {
  scopes: ScopeInfo[]
  value: string[]
  onChange: (scopes: string[]) => void
  /** Scopes that can't be unticked, with the reason shown in place of their description. */
  locked?: string[]
}) => {
  const id = useId()
  return (
    <Fieldset legend="Scopes" className="space-y-2">
      {scopes.map((scope) => {
        const isLocked = locked.includes(scope.value) && value.includes(scope.value)
        return (
          <div key={scope.value}>
            <Checkbox label={scope.label} checked={value.includes(scope.value)} disabled={isLocked}
              aria-describedby={`${id}-${scope.value}`} onChange={() => onChange(toggleScope(value, scope.value))} />
            <Hint id={`${id}-${scope.value}`} className="ml-6">
              {isLocked ? "You can't remove this from yourself: you'd lose access to user management." : scope.description}
            </Hint>
          </div>
        )
      })}
    </Fieldset>
  )
}

interface UserFormData {
  name: string
  email: string
  password: string
  user_type: 'human' | 'bot'
  scopes: string[]
}

const UserManagement = () => {
  const { listUsers, listScopes, createUser, updateUser, deleteUser, regenerateApiKey, resetPassword } =
    useUsers()
  const { user: currentUser, hasScope } = useAuth()
  const multiUser = useModule('multi_user')

  const [users, setUsers] = useState<User[]>([])
  const [availableScopes, setAvailableScopes] = useState<ScopeInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [resettingUser, setResettingUser] = useState<User | null>(null)

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
  const [emailError, setEmailError] = useState<string | null>(null)
  const emailRef = useRef<HTMLInputElement>(null)
  useEffect(() => { if (emailError) emailRef.current?.focus() }, [emailError])

  // Edit modal state
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [editForm, setEditForm] = useState<UserUpdate>({})
  const [editLoading, setEditLoading] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)


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

  // The server refuses a second human without multi_user; say so before the form is filled in.
  const humanLocked = !multiUser && users.some(u => u.user_type === 'human')
  const userType = humanLocked ? 'bot' : createForm.user_type

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreateLoading(true)
    setCreateError(null)
    setEmailError(null)

    try {
      const data: UserCreate = {
        name: createForm.name,
        email: createForm.email,
        user_type: userType,
        scopes: createForm.scopes,
      }

      if (userType === 'human') {
        if (!createForm.password) {
          setCreateError('Password is required for human users')
          setCreateLoading(false)
          return
        }
        data.password = createForm.password
      }

      await createUser(data)
      closeCreateModal()
      await loadUsers()
    } catch (e) {
      const onEmail = e instanceof ApiError ? e.fields.email : undefined
      if (onEmail) setEmailError(onEmail)
      else setCreateError(e instanceof Error ? e.message : 'Failed to create user')
    } finally {
      setCreateLoading(false)
    }
  }

  const handleEdit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!editingUser) return

    setEditLoading(true)
    setEditError(null)

    try {
      await updateUser(editingUser.id, editForm)
      closeEditModal()
      await loadUsers()
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Failed to update user')
    } finally {
      setEditLoading(false)
    }
  }

  const handleDelete = async (user: User) => {
    try {
      await deleteUser(user.id)
      await loadUsers()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete user')
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

  const closeCreateModal = () => {
    setShowCreateModal(false)
    setCreateForm({ name: '', email: '', password: '', user_type: 'human', scopes: ['read'] })
    setCreateError(null)
    setEmailError(null)
  }

  const closeEditModal = () => {
    setEditingUser(null)
    setEditForm({})
    setEditError(null)
  }

  const closeApiKeyModal = () => {
    setApiKeyUser(null)
    setNewApiKey(null)
  }

  if (loading) {
    return <Page title="User management" width="default" loading />
  }

  return (
    <>
      <Page
        title="User management"
        actions={<>
          <Button variant="primary" onClick={() => setShowCreateModal(true)}>New user</Button>
        </>}
        width="narrow"
      >

        {/* Error message */}
        {error && (
          <Notice tone="error" onDismiss={() => setError(null)}>{error}</Notice>
        )}
        {success && <Notice tone="success" onDismiss={() => setSuccess(null)}>{success}</Notice>}

        {users.length === 0 ? (
          <EmptyState message="No users found. Create your first user above." />
        ) : (
          <List>
            {users.map((user) => (
              <Row
                key={user.id}
                title={<>{user.name}{user.id === currentUser?.id && <span className="ml-2 text-xs font-normal text-slate-500">(you)</span>}</>}
                badges={<>
                  <Badge tone={user.user_type === 'human' ? 'info' : 'primary'}>{user.user_type === 'human' ? 'Human' : 'Bot'}</Badge>
                  {user.scopes.map(scope => <Badge key={scope}>{scope === '*' ? 'All permissions' : scope}</Badge>)}
                </>}
                meta={[user.email, user.api_key_count > 0 && 'Has API key configured']}
                actions={<>
                  {user.user_type === 'bot' && (
                    <Button size="sm" onClick={() => setApiKeyUser(user)}>
                      {user.api_key_count > 0 ? 'Regenerate key' : 'Generate key'}
                    </Button>
                  )}
                  {user.user_type === 'human' && user.id !== currentUser?.id && (
                    <Button size="sm" onClick={() => setResettingUser(user)}>Reset password</Button>
                  )}
                  <Button size="sm" onClick={() => {
                    setEditingUser(user)
                    setEditForm({ name: user.name, email: user.email, scopes: [...user.scopes] })
                  }}>
                    Edit
                  </Button>
                  {user.id !== currentUser?.id && (
                    <ConfirmButton confirm={`Delete ${user.name}? This can't be undone.`} confirmLabel="Delete user" onConfirm={() => handleDelete(user)} />
                  )}
                </>}
              />
            ))}
          </List>
        )}
      </Page>

      {showCreateModal && (
        <Modal title="New user" onClose={closeCreateModal} size="md">
          <form onSubmit={handleCreate} className="space-y-4">
            {createError && <Notice tone="error">{createError}</Notice>}

            <Field label="User type" hint={humanLocked && <>
              This server allows one human user. Turn on the <code>multi_user</code> module in {hasScope('admin') ? <Link className="underline" to="/ui/server-settings">Server settings</Link> : 'Server settings'} for more.
            </>}>
              <Select
                value={userType}
                onChange={(e) =>
                  setCreateForm({ ...createForm, user_type: e.target.value as 'human' | 'bot' })
                }
              >
                <option value="human" disabled={humanLocked}>Human</option>
                <option value="bot">Bot</option>
              </Select>
            </Field>

            <Field label="Name">
              <Input
                type="text"
                autoComplete="off"
                value={createForm.name}
                onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
              />
            </Field>

            <Field label="Email" error={emailError}>
              <Input
                ref={emailRef}
                type="email"
                autoComplete="off"
                value={createForm.email}
                onChange={(e) => { setCreateForm({ ...createForm, email: e.target.value }); setEmailError(null) }}
              />
            </Field>

            {userType === 'human' && (
              <Field label="Password" hint="At least 8 characters">
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={createForm.password}
                  onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                />
              </Field>
            )}

            <ScopeFieldset scopes={availableScopes} value={createForm.scopes}
              onChange={(scopes) => setCreateForm({ ...createForm, scopes })} />

            <FormActions>
              <Button type="button" onClick={closeCreateModal}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={createLoading}>
                {createLoading ? 'Creating...' : 'Create user'}
              </Button>
            </FormActions>
          </form>
        </Modal>
      )}

      {editingUser && (
        <Modal title="Edit user" onClose={closeEditModal} size="md">
          <form onSubmit={handleEdit} className="space-y-4">
            {editError && <Notice tone="error">{editError}</Notice>}

            <Field label="Name">
              <Input
                type="text"
                autoComplete="off"
                value={editForm.name || ''}
                onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
              />
            </Field>

            <Field label="Email">
              <Input
                type="email"
                autoComplete="off"
                value={editForm.email || ''}
                onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
              />
            </Field>

            <ScopeFieldset scopes={availableScopes} value={editForm.scopes || []}
              onChange={(scopes) => setEditForm({ ...editForm, scopes })}
              locked={editingUser.id === currentUser?.id ? ['*'] : []} />

            <FormActions>
              <Button type="button" onClick={closeEditModal}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={editLoading}>
                {editLoading ? 'Saving...' : 'Save changes'}
              </Button>
            </FormActions>
          </form>
        </Modal>
      )}

      {resettingUser && (
        <ResetPasswordModal user={resettingUser} resetPassword={resetPassword}
          onClose={() => setResettingUser(null)}
          onDone={() => {
            setSuccess(`Password reset for ${resettingUser.name}. They have been signed out everywhere.`)
            setResettingUser(null)
          }} />
      )}

      {/* API Key Modal */}
      {apiKeyUser && (
        <Modal size="md" onClose={newApiKey ? undefined : closeApiKeyModal}
          title={newApiKey ? 'New API key generated' : `${apiKeyUser.api_key_count > 0 ? 'Regenerate' : 'Generate'} API key`}>
          {newApiKey ? <NewApiKey apiKey={newApiKey} onDone={closeApiKeyModal} /> : (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">
                {apiKeyUser.api_key_count > 0
                  ? `Are you sure you want to regenerate the API key for ${apiKeyUser.name}? The current key will be invalidated.`
                  : `Generate an API key for ${apiKeyUser.name}.`}
              </p>
              <FormActions>
                <Button onClick={closeApiKeyModal}>Cancel</Button>
                <Button variant={apiKeyUser.api_key_count > 0 ? 'dangerSolid' : 'primary'}
                  onClick={handleRegenerateApiKey} disabled={apiKeyLoading}>
                  {apiKeyLoading ? 'Generating...' : apiKeyUser.api_key_count > 0 ? 'Regenerate' : 'Generate'}
                </Button>
              </FormActions>
            </div>
          )}
        </Modal>
      )}
    </>
  )
}

const ResetPasswordModal = ({ user, resetPassword, onClose, onDone }: {
  user: User
  resetPassword: (id: number, newPassword: string) => Promise<void>
  onClose: () => void
  onDone: () => void
}) => {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitted(true)
    setError(null)
    if (!passwordIsValid(password, confirm)) return
    setSaving(true)
    try {
      await resetPassword(user.id, password)
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reset password')
      setSaving(false)
    }
  }

  return (
    <Modal title={`Reset password for ${user.name}`} onClose={onClose} size="md">
      <form onSubmit={handleSubmit} noValidate className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}
        <p className="text-sm text-slate-600">
          {user.name} will be signed out everywhere and will need this password to log back in. Share it with them privately.
        </p>
        <NewPasswordFields password={password} confirm={confirm} submitted={submitted}
          onPassword={setPassword} onConfirm={setConfirm} />
        <FormActions>
          <Button type="button" onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={saving}>{saving ? 'Saving...' : 'Reset password'}</Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default UserManagement
