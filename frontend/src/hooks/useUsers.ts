import { useCallback } from 'react'
import { useAuth } from './useAuth'

export interface User {
  id: number
  name: string
  email: string
  user_type: 'human' | 'bot'
  scopes: string[]
  has_api_key: boolean
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
}

export interface PasswordChange {
  current_password: string
  new_password: string
}

export interface ApiKeyResponse {
  api_key: string
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
      throw new Error('Failed to fetch users')
    }
    return response.json()
  }, [apiCall])

  const listScopes = useCallback(async (): Promise<ScopeInfo[]> => {
    const response = await apiCall('/users/scopes')
    if (!response.ok) {
      if (response.status === 403) throw new Error('Insufficient permissions')
      throw new Error('Failed to fetch available scopes')
    }
    return response.json()
  }, [apiCall])

  const getUser = useCallback(async (id: number): Promise<User> => {
    const response = await apiCall(`/users/${id}`)
    if (!response.ok) {
      if (response.status === 403) throw new Error('Insufficient permissions')
      if (response.status === 404) throw new Error('User not found')
      throw new Error('Failed to fetch user')
    }
    return response.json()
  }, [apiCall])

  const getCurrentUser = useCallback(async (): Promise<User> => {
    const response = await apiCall('/users/me')
    if (!response.ok) throw new Error('Failed to fetch current user')
    return response.json()
  }, [apiCall])

  const createUser = useCallback(async (data: UserCreate): Promise<User> => {
    const response = await apiCall('/users', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to create user')
    }
    return response.json()
  }, [apiCall])

  const updateUser = useCallback(async (id: number, data: UserUpdate): Promise<User> => {
    const response = await apiCall(`/users/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update user')
    }
    return response.json()
  }, [apiCall])

  const deleteUser = useCallback(async (id: number): Promise<void> => {
    const response = await apiCall(`/users/${id}`, { method: 'DELETE' })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to delete user')
    }
  }, [apiCall])

  const regenerateApiKey = useCallback(async (id: number): Promise<ApiKeyResponse> => {
    const response = await apiCall(`/users/${id}/regenerate-api-key`, { method: 'POST' })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to regenerate API key')
    }
    return response.json()
  }, [apiCall])

  const changePassword = useCallback(async (data: PasswordChange): Promise<void> => {
    const response = await apiCall('/users/me/change-password', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to change password')
    }
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
  }
}
