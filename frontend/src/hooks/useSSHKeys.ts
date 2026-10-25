import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { apiError } from './apiError'

export interface SSHKey {
  id: number
  name: string
  user_id: number | null
  public_key: string
  fingerprint: string | null
  is_default: boolean
}
export interface SSHKeyInput {
  name: string
  private_key?: string
  passphrase?: string
  generate?: boolean
  instance?: boolean
}
export function useSSHKeys() {
  const { apiCall } = useAuth()
  const request = useCallback(async (path: string, method = 'GET', body?: unknown) => {
    const response = await apiCall(`/ssh-keys${path}`, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    if (!response.ok) throw await apiError(response, 'SSH key operation failed')
    return response.json()
  }, [apiCall])
  const list = useCallback((): Promise<SSHKey[]> => request(''), [request])
  const save = useCallback((body: SSHKeyInput, id?: number): Promise<SSHKey> => request(id === undefined ? '' : `/${id}`, id === undefined ? 'POST' : 'PUT', body), [request])
  const remove = useCallback((id: number) => request(`/${id}`, 'DELETE'), [request])
  const setDefault = useCallback((key_id: number | null) => request('/default', 'PUT', { key_id }), [request])
  return { list, save, remove, setDefault }
}
