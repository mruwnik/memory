import { SERVER_URL } from '@/hooks/useAuth'

/** Link to a stored file; each path segment is encoded, so `#`, `?` and `%` in a name stay part of it. */
export const fileUrl = (path: string) => `${SERVER_URL}/files/${path.split('/').map(encodeURIComponent).join('/')}`
