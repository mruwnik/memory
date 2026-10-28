import { useState, useEffect, useCallback } from 'react'
import { useUsers, type User, type UserUpdate } from '../../hooks/useUsers'
import { useAuth } from '../../hooks/useAuth'
import { Badge, Button, Card, Field, FormActions, Input, List, Modal, Notice, Page, Row } from '@/components/shared/ui'
import { TimezoneCombobox } from '@/components/shared/TimezoneCombobox'
import { ApiKeys } from './ApiKeys'
import { NewPasswordFields, passwordIsValid } from './NewPasswordFields'

const UserSettings = () => {
  const { getCurrentUser, updateUser, changePassword } = useUsers()
  const { user: authUser, checkAuth } = useAuth()

  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Form states
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [timezone, setTimezone] = useState('UTC')
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  // Password change modal
  const [showPasswordModal, setShowPasswordModal] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [passwordSaving, setPasswordSaving] = useState(false)

  const loadUser = useCallback(async () => {
    setLoading(true)
    try {
      const userData = await getCurrentUser()
      setUser(userData)
      setName(userData.name)
      setEmail(userData.email)
      setTimezone(userData.timezone)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load user')
    } finally {
      setLoading(false)
    }
  }, [getCurrentUser])

  useEffect(() => {
    loadUser()
  }, [loadUser])

  const profileChanged = !!user && (name !== user.name || email !== user.email || timezone !== user.timezone)

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!user || !profileChanged) return

    setSaving(true)
    setSaveMessage(null)

    try {
      const updates: UserUpdate = {}
      if (name !== user.name) updates.name = name
      if (email !== user.email) updates.email = email
      if (timezone !== user.timezone) updates.timezone = timezone

      const updated = await updateUser(user.id, updates)
      setUser(updated)
      setSaveMessage({ type: 'success', text: 'Profile updated successfully' })
      // Refresh auth state to update the header
      await checkAuth()
    } catch (e) {
      setSaveMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to update profile' })
    } finally {
      setSaving(false)
    }
  }

  const [passwordSubmitted, setPasswordSubmitted] = useState(false)

  const closePasswordModal = () => {
    setShowPasswordModal(false)
    setCurrentPassword('')
    setNewPassword('')
    setConfirmPassword('')
    setPasswordError(null)
    setPasswordSubmitted(false)
  }

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setPasswordError(null)
    setPasswordSubmitted(true)
    if (!passwordIsValid(newPassword, confirmPassword)) return

    setPasswordSaving(true)

    try {
      await changePassword({ current_password: currentPassword, new_password: newPassword })
      closePasswordModal()
      setSaveMessage({ type: 'success', text: 'Password changed successfully' })
    } catch (e) {
      setPasswordError(e instanceof Error ? e.message : 'Failed to change password')
    } finally {
      setPasswordSaving(false)
    }
  }

  if (loading) {
    return <Page title="User settings" width="default" loading />
  }

  if (error && !user) {
    return <Page title="User settings" width="default" error={error} onRetry={loadUser} />
  }

  return (
    <>
      <Page
        title="User settings"
        width="default"
      >

        {saveMessage && (
          <Notice tone={saveMessage.type === 'success' ? 'success' : 'error'} onDismiss={() => setSaveMessage(null)}>{saveMessage.text}</Notice>
        )}

        <Card title="Profile">
          <form onSubmit={handleSaveProfile} className="space-y-4">
            <Field label="Name">
              <Input type="text" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Email">
              <Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="Timezone" hint="Scheduled tasks and agent schedules run on this clock. Pick one or type any IANA name, such as Asia/Kolkata.">
              <TimezoneCombobox value={timezone} onChange={setTimezone} />
            </Field>
            <FormActions>
              <Button variant="primary" type="submit" disabled={saving || !profileChanged}>{saving ? 'Saving...' : 'Save changes'}</Button>
            </FormActions>
          </form>
        </Card>

        {authUser?.user_type === 'human' && (
          <Card title="Security" flush>
            <List flush className="border-t border-slate-100">
              <Row title="Password" meta={['Change your account password']}
                actions={<Button size="sm" onClick={() => setShowPasswordModal(true)}>Change password</Button>} />
            </List>
          </Card>
        )}

        {user && <ApiKeys user={user} />}

        <Card title="Permissions" description="Your current scopes:">
          <div className="flex flex-wrap gap-2">
            {user?.scopes.map((scope) => <Badge key={scope}>{scope === '*' ? 'All permissions' : scope}</Badge>)}
          </div>
        </Card>
      </Page>

      {showPasswordModal && (
        <Modal title="Change password" onClose={closePasswordModal} size="md">
          <form onSubmit={handleChangePassword} noValidate className="space-y-4">
            {passwordError && <Notice tone="error">{passwordError}</Notice>}

            {/* Lets password managers tell which account the new password belongs to */}
            <input type="text" autoComplete="username" value={user?.email ?? ''} readOnly hidden />

            <Field label="Current password">
              <Input type="password" autoComplete="current-password" value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)} />
            </Field>

            <NewPasswordFields password={newPassword} confirm={confirmPassword} submitted={passwordSubmitted}
              onPassword={setNewPassword} onConfirm={setConfirmPassword} />

            <FormActions>
              <Button type="button" onClick={closePasswordModal}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={passwordSaving}>
                {passwordSaving ? 'Saving...' : 'Change password'}
              </Button>
            </FormActions>
          </form>
        </Modal>
      )}
    </>
  )
}

export default UserSettings
