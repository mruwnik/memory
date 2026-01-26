import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useUsers, type User } from '../../hooks/useUsers'
import { useAuth } from '../../hooks/useAuth'

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
    return (
      <div className="min-h-screen bg-slate-50 p-8 flex items-center justify-center">
        <p className="text-slate-500">Loading...</p>
      </div>
    )
  }

  if (error && !user) {
    return (
      <div className="min-h-screen bg-slate-50 p-8">
        <div className="max-w-2xl mx-auto">
          <p className="text-red-600">{error}</p>
          <button onClick={loadUser} className="mt-4 text-primary hover:underline">
            Try again
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50 p-8">
      <div className="max-w-2xl mx-auto">
        {/* Header */}
        <header className="flex items-center gap-4 mb-8 pb-4 border-b border-slate-200">
          <Link
            to="/ui/dashboard"
            className="bg-slate-100 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-200 transition-colors"
          >
            &larr; Back
          </Link>
          <h1 className="text-2xl font-semibold text-slate-800 flex-1">User Settings</h1>
        </header>

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
              <label className="block text-sm font-medium text-slate-700 mb-1">Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
            </div>

            <button
              onClick={handleSaveProfile}
              disabled={saving}
              className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 disabled:bg-slate-400 transition-colors"
            >
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
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
                <button
                  onClick={() => setShowPasswordModal(true)}
                  className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
                >
                  Change Password
                </button>
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
              <button
                onClick={() => setShowApiKeyModal(true)}
                className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
              >
                {(user?.api_key_count ?? 0) > 0 ? 'Regenerate' : 'Generate'} API Key
              </button>
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
      </div>

      {/* Password Change Modal */}
      {showPasswordModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md m-4">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">Change Password</h3>

            {passwordError && (
              <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg text-sm">{passwordError}</div>
            )}

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Current Password</label>
                <input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">New Password</label>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Confirm New Password</label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => {
                  setShowPasswordModal(false)
                  setCurrentPassword('')
                  setNewPassword('')
                  setConfirmPassword('')
                  setPasswordError(null)
                }}
                className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleChangePassword}
                disabled={passwordSaving}
                className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 disabled:bg-slate-400 transition-colors"
              >
                {passwordSaving ? 'Saving...' : 'Change Password'}
              </button>
            </div>
          </div>
        </div>
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
                  <button
                    onClick={() => setShowApiKeyModal(false)}
                    className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleRegenerateApiKey}
                    disabled={apiKeyLoading}
                    className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary/90 disabled:bg-slate-400 transition-colors"
                  >
                    {apiKeyLoading ? 'Generating...' : (user?.api_key_count ?? 0) > 0 ? 'Regenerate' : 'Generate'}
                  </button>
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
                  <button
                    onClick={() => copyToClipboard(newApiKey)}
                    className="bg-slate-100 text-slate-700 py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors"
                  >
                    Copy
                  </button>
                  <button
                    onClick={() => {
                      setShowApiKeyModal(false)
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

export default UserSettings
