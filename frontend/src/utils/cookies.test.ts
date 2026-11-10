import { describe, it, expect, beforeEach } from 'vitest'
import { clearCookies } from '@/test/utils'
import { cookieString, deleteCookie, getCookie, setCookie } from './cookies'

const EXPIRES = new Date('2030-01-01T00:00:00Z')

beforeEach(() => {
  clearCookies()
})

describe('cookieString', () => {
  it.each([
    ['https:', true],
    ['http:', false],
  ])('on %s marks the cookie Secure: %s', (protocol, secure) => {
    const cookie = cookieString('session_id', 'tok', EXPIRES, protocol)
    expect(cookie.endsWith(';Secure')).toBe(secure)
    expect(cookie).toContain('session_id=tok;')
    expect(cookie).toContain(';path=/;SameSite=Lax')
  })

  it('clears with an expiry in the past', () => {
    const cookie = cookieString('session_id', '', new Date(1000), 'https:')
    expect(cookie).toContain('expires=Thu, 01 Jan 1970 00:00:01 GMT')
    expect(cookie.endsWith(';Secure')).toBe(true)
  })
})

describe('setCookie / getCookie / deleteCookie', () => {
  it('round-trips a value and removes it', () => {
    setCookie('access_token', 'abc', 1)
    expect(getCookie('access_token')).toBe('abc')
    deleteCookie('access_token')
    expect(getCookie('access_token')).toBeNull()
  })
})
