import { SERVER_URL } from './useAuth'
import { apiError } from './apiError'

const post = async (path: string, body: object) => {
  const response = await fetch(`${SERVER_URL}/auth/password-reset${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw await apiError(response, 'Request failed')
}

export const getResetAvailable = async (): Promise<boolean> => {
  const response = await fetch(`${SERVER_URL}/auth/password-reset`)
  if (!response.ok) throw await apiError(response, 'Could not check password reset')
  return (await response.json()).available
}

export const requestReset = (email: string) => post('/request', { email })

export const confirmReset = (token: string, newPassword: string) =>
  post('/confirm', { token, new_password: newPassword })
