import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { apiError } from './apiError'

export interface User {
  id: number
  name: string
  email: string
  user_type: 'human' | 'bot'
  scopes: string[]
  timezone: string
  api_key_count: number
  created_at?: string
}

export interface UserCreate {
  name: string
  email: string
  password?: string
  user_type?: 'human' | 'bot'
  scopes?: string[]
}

export interface UserUpdate {
  name?: string
  email?: string
  scopes?: string[]
  timezone?: string
}

export interface PasswordChange {
  current_password: string
  new_password: string
}

export interface ApiKeyResponse {
  key: string
}

export interface ScopeInfo {
  value: string
  label: string
  description: string
  category: string
}

export const useUsers = () => {
  const { apiCall } = useAuth()

  const listUsers = useCallback(async (): Promise<User[]> => {
    const response = await apiCall('/users')
    if (!response.ok) {
      if (response.status === 403) throw new Error('Insufficient permissions')
      throw await apiError(response, 'Failed to fetch users')
    }
    return response.json()
  }, [apiCall])

  const listScopes = useCallback(async (): Promise<ScopeInfo[]> => {
    const response = await apiCall('/users/scopes')
    if (!response.ok) {
      if (response.status === 403) throw new Error('Insufficient permissions')
      throw await apiError(response, 'Failed to fetch available scopes')
    }
    return response.json()
  }, [apiCall])

  const getUser = useCallback(async (id: number): Promise<User> => {
    const response = await apiCall(`/users/${id}`)
    if (!response.ok) {
      if (response.status === 403) throw new Error('Insufficient permissions')
      if (response.status === 404) throw new Error('User not found')
      throw await apiError(response, 'Failed to fetch user')
    }
    return response.json()
  }, [apiCall])

  const getCurrentUser = useCallback(async (): Promise<User> => {
    const response = await apiCall('/users/me')
    if (!response.ok) throw await apiError(response, 'Failed to fetch current user')
    return response.json()
  }, [apiCall])

  const createUser = useCallback(async (data: UserCreate): Promise<User> => {
    const response = await apiCall('/users', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) throw await apiError(response, 'Failed to create user')
    return response.json()
  }, [apiCall])

  const updateUser = useCallback(async (id: number, data: UserUpdate): Promise<User> => {
    const response = await apiCall(`/users/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) throw await apiError(response, 'Failed to update user')
    return response.json()
  }, [apiCall])

  const deleteUser = useCallback(async (id: number): Promise<void> => {
    const response = await apiCall(`/users/${id}`, { method: 'DELETE' })
    if (!response.ok) throw await apiError(response, 'Failed to delete user')
  }, [apiCall])

  const regenerateApiKey = useCallback(async (id: number): Promise<ApiKeyResponse> => {
    const response = await apiCall(`/users/${id}/api-keys`, {
      method: 'POST',
      body: JSON.stringify({ name: 'Default API Key' }),
    })
    if (!response.ok) throw await apiError(response, 'Failed to regenerate API key')
    return response.json()
  }, [apiCall])

  const changePassword = useCallback(async (data: PasswordChange): Promise<void> => {
    const response = await apiCall('/users/me/change-password', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) throw await apiError(response, 'Failed to change password')
  }, [apiCall])

  // Admin-only; the server also signs the user out everywhere
  const resetPassword = useCallback(async (id: number, newPassword: string): Promise<void> => {
    const response = await apiCall(`/users/${id}/reset-password`, {
      method: 'POST',
      body: JSON.stringify({ new_password: newPassword }),
    })
    if (!response.ok) throw await apiError(response, 'Failed to reset password')
  }, [apiCall])

  return {
    listUsers,
    listScopes,
    getUser,
    getCurrentUser,
    createUser,
    updateUser,
    deleteUser,
    regenerateApiKey,
    changePassword,
    resetPassword,
  }
}
