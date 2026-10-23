import { useState, useEffect, useCallback } from 'react'
import { useUsers, type User } from '../../hooks/useUsers'
import { useAuth } from '../../hooks/useAuth'
import { Button, Input, Label, Modal, Notice, Page } from '@/components/shared/ui'

const UserSettings = () => {
  const { getCurrentUser, updateUser, changePassword, regenerateApiKey } = useUsers()
  const { user: authUser, checkAuth } = useAuth()

  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Form states
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  // Password change modal
  const [showPasswordModal, setShowPasswordModal] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [passwordSaving, setPasswordSaving] = useState(false)

  // API key regeneration modal
  const [showApiKeyModal, setShowApiKeyModal] = useState(false)
  const [newApiKey, setNewApiKey] = useState<string | null>(null)
  const [apiKeyLoading, setApiKeyLoading] = useState(false)

  const loadUser = useCallback(async () => {
    setLoading(true)
    try {
      const userData = await getCurrentUser()
      setUser(userData)
      setName(userData.name)
      setEmail(userData.email)
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

  const handleSaveProfile = async () => {
    if (!user) return

    setSaving(true)
    setSaveMessage(null)

    try {
      const updates: { name?: string; email?: string } = {}
      if (name !== user.name) updates.name = name
      if (email !== user.email) updates.email = email

      if (Object.keys(updates).length === 0) {
        setSaveMessage({ type: 'success', text: 'No changes to save' })
        setSaving(false)
        return
      }

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

  const handleChangePassword = async () => {
    setPasswordError(null)

    if (newPassword !== confirmPassword) {
      setPasswordError('Passwords do not match')
      return
    }

    if (newPassword.length < 8) {
      setPasswordError('Password must be at least 8 characters')
      return
    }

    setPasswordSaving(true)

    try {
      await changePassword({ current_password: currentPassword, new_password: newPassword })
      setShowPasswordModal(false)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setSaveMessage({ type: 'success', text: 'Password changed successfully' })
    } catch (e) {
      setPasswordError(e instanceof Error ? e.message : 'Failed to change password')
    } finally {
      setPasswordSaving(false)
    }
  }

  const handleRegenerateApiKey = async () => {
    if (!user) return

    setApiKeyLoading(true)

    try {
      const result = await regenerateApiKey(user.id)
      setNewApiKey(result.key)
      setUser({ ...user, api_key_count: (user.api_key_count || 0) + 1 })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to regenerate API key')
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

  if (loading) {
    return <Page title="User Settings" width="narrow" loading />
  }

  if (error && !user) {
    return <Page title="User Settings" width="narrow" error={error} onRetry={loadUser} />
  }

  return (
    <>
      <Page
        title="User Settings"
        width="narrow"
      >

        {/* Save message */}
        {saveMessage && (
          <div
            className={`mb-6 p-4 rounded-lg ${
              saveMessage.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
            }`}
          >
            {saveMessage.text}
          </div>
        )}

        {/* Profile Section */}
        <section className="bg-white rounded-xl shadow-md p-6 mb-6">
          <h2 className="text-lg font-semibold text-slate-800 mb-4">Profile</h2>

          <div className="space-y-4">
            <div>
              <Label className="mb-1">Name</Label>
              <Input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <div>
              <Label className="mb-1">Email</Label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            <Button variant="primary"
              onClick={handleSaveProfile}
              disabled={saving}
            >
              {saving ? 'Saving...' : 'Save Changes'}
            </Button>
          </div>
        </section>

        {/* Security Section */}
        <section className="bg-white rounded-xl shadow-md p-6 mb-6">
          <h2 className="text-lg font-semibold text-slate-800 mb-4">Security</h2>

          <div className="space-y-4">
            {/* Password */}
            {authUser?.user_type === 'human' && (
              <div className="flex items-center justify-between py-3 border-b border-slate-100">
                <div>
                  <p className="font-medium text-slate-800">Password</p>
                  <p className="text-sm text-slate-500">Change your account password</p>
                </div>
                <Button
                  onClick={() => setShowPasswordModal(true)}
                >
                  Change Password
                </Button>
              </div>
            )}

            {/* API Key */}
            <div className="flex items-center justify-between py-3">
              <div>
                <p className="font-medium text-slate-800">API Key</p>
                <p className="text-sm text-slate-500">
                  {(user?.api_key_count ?? 0) > 0 ? 'You have an API key configured' : 'No API key configured'}
                </p>
              </div>
              <Button
                onClick={() => setShowApiKeyModal(true)}
              >
                {(user?.api_key_count ?? 0) > 0 ? 'Regenerate' : 'Generate'} API Key
              </Button>
            </div>
          </div>
        </section>

        {/* Permissions Section */}
        <section className="bg-white rounded-xl shadow-md p-6">
          <h2 className="text-lg font-semibold text-slate-800 mb-4">Permissions</h2>

          <div>
            <p className="text-sm text-slate-500 mb-2">Your current scopes:</p>
            <div className="flex flex-wrap gap-2">
              {user?.scopes.map((scope) => (
                <span
                  key={scope}
                  className="bg-slate-100 text-slate-700 px-3 py-1 rounded-full text-sm"
                >
                  {scope}
                </span>
              ))}
            </div>
          </div>
        </section>
      </Page>

      {/* Password Change Modal */}
      {showPasswordModal && (
        <Modal title="Change Password" onClose={() => {
                  setShowPasswordModal(false)
                  setCurrentPassword('')
                  setNewPassword('')
                  setConfirmPassword('')
                  setPasswordError(null)
                }} size="md">
          {passwordError && (
              <Notice tone="error" className="mb-4">{passwordError}</Notice>
            )}

            <div className="space-y-4">
              <div>
                <Label className="mb-1">Current Password</Label>
                <Input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                />
              </div>

              <div>
                <Label className="mb-1">New Password</Label>
                <Input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                />
              </div>

              <div>
                <Label className="mb-1">Confirm New Password</Label>
                <Input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <Button
                onClick={() => {
                  setShowPasswordModal(false)
                  setCurrentPassword('')
                  setNewPassword('')
                  setConfirmPassword('')
                  setPasswordError(null)
                }}
              >
                Cancel
              </Button>
              <Button variant="primary"
                onClick={handleChangePassword}
                disabled={passwordSaving}
              >
                {passwordSaving ? 'Saving...' : 'Change Password'}
              </Button>
            </div>
        </Modal>
      )}

      {/* API Key Modal */}
      {showApiKeyModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md m-4">
            {!newApiKey ? (
              <>
                <h3 className="text-lg font-semibold text-slate-800 mb-4">
                  {(user?.api_key_count ?? 0) > 0 ? 'Regenerate' : 'Generate'} API Key
                </h3>

                <p className="text-sm text-slate-600 mb-4">
                  {(user?.api_key_count ?? 0) > 0
                    ? 'Are you sure you want to regenerate your API key? The current key will be invalidated and any integrations using it will stop working.'
                    : 'Generate an API key to use with integrations and CLI tools.'}
                </p>

                <div className="flex justify-end gap-3">
                  <Button
                    onClick={() => setShowApiKeyModal(false)}
                  >
                    Cancel
                  </Button>
                  <Button variant="primary"
                    onClick={handleRegenerateApiKey}
                    disabled={apiKeyLoading}
                  >
                    {apiKeyLoading ? 'Generating...' : (user?.api_key_count ?? 0) > 0 ? 'Regenerate' : 'Generate'}
                  </Button>
                </div>
              </>
            ) : (
              <>
                <h3 className="text-lg font-semibold text-slate-800 mb-4">New API Key Generated</h3>

                <p className="text-sm text-slate-600 mb-4">
                  Copy your new API key now. It won't be shown again.
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
                      setShowApiKeyModal(false)
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

export default UserSettings
