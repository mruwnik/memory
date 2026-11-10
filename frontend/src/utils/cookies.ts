// Auth cookies written from JS. On https they carry Secure so the browser never
// sends a session token over plain http.

const EXPIRED = new Date(1000)

const isSecureProtocol = (protocol: string) => protocol === 'https:'

export const cookieString = (
  name: string,
  value: string,
  expires: Date,
  protocol: string = window.location.protocol,
) => {
  const secure = isSecureProtocol(protocol) ? ';Secure' : ''
  return `${name}=${value};expires=${expires.toUTCString()};path=/;SameSite=Lax${secure}`
}

export const getCookie = (name: string) => {
  const parts = `; ${document.cookie}`.split(`; ${name}=`)
  if (parts.length === 2) return parts[1].split(';')[0]
  return null
}

export const setCookie = (name: string, value: string, days = 30) => {
  const expires = new Date(Date.now() + days * 24 * 60 * 60 * 1000)
  document.cookie = cookieString(name, value, expires)
}

export const deleteCookie = (name: string) => {
  document.cookie = cookieString(name, '', EXPIRED)
}
