import { useCallback } from 'react'
import { useAuth } from './useAuth'

// Types for Secrets
export interface Secret {
  id: number
  name: string
  description: string | null
  created_at: string
  updated_at: string
}

export interface SecretWithValue extends Secret {
  value: string
}

export interface SecretCreate {
  name: string
  value: string
  description?: string
}

export interface SecretUpdate {
  value?: string
  description?: string
}

export const useSecrets = () => {
  const { apiCall } = useAuth()

  const listSecrets = useCallback(async (): Promise<Secret[]> => {
    const response = await apiCall('/secrets')
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to list secrets')
    }
    return response.json()
  }, [apiCall])

  const createSecret = useCallback(async (data: SecretCreate): Promise<Secret> => {
    const response = await apiCall('/secrets', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to create secret')
    }
    return response.json()
  }, [apiCall])

  const updateSecret = useCallback(async (id: number, data: SecretUpdate): Promise<Secret> => {
    const response = await apiCall(`/secrets/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update secret')
    }
    return response.json()
  }, [apiCall])

  const deleteSecret = useCallback(async (id: number): Promise<void> => {
    const response = await apiCall(`/secrets/${id}`, { method: 'DELETE' })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to delete secret')
    }
  }, [apiCall])

  const getSecretValue = useCallback(async (id: number): Promise<SecretWithValue> => {
    const response = await apiCall(`/secrets/${id}/value`)
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to get secret value')
    }
    return response.json()
  }, [apiCall])

  return {
    listSecrets,
    createSecret,
    updateSecret,
    deleteSecret,
    getSecretValue,
  }
}
