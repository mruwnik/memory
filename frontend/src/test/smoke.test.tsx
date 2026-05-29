import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { screen } from '@testing-library/react'
import { useDebounce } from '@/hooks/useDebounce'
import { useAuth } from '@/hooks/useAuth'
import { PRIORITY_ORDER } from '@/constants/priority'
import { renderWithRouter, mockFetch, mockResponse, setAuthCookies, clearCookies } from './utils'

// Confirms the harness wiring works: pure module import, hook + fetch mock,
// and component render-with-router. If this file fails, the infra is broken;
// individual feature tests are downstream of it.

describe('test harness smoke', () => {
  beforeEach(() => {
    clearCookies()
  })

  it('imports pure constants', () => {
    expect(PRIORITY_ORDER.urgent).toBe(0)
  })

  it('runs a hook with fake timers behaviour (useDebounce)', () => {
    const { result, rerender } = renderHook(({ v }) => useDebounce(v, 50), {
      initialProps: { v: 'a' },
    })
    expect(result.current).toBe('a')
    rerender({ v: 'b' })
    // immediately after, still the old value
    expect(result.current).toBe('a')
  })

  it('mocks fetch for an auth hook', async () => {
    setAuthCookies()
    mockFetch(async () =>
      mockResponse({
        json: { user_id: 1, name: 'Test', email: 't@e.com', user_type: 'human', scopes: ['*'] },
      }),
    )
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true))
    expect(result.current.user?.name).toBe('Test')
    expect(result.current.hasScope('anything')).toBe(true)
  })

  it('renders a component inside a router', () => {
    const Dummy = () => <div>hello harness</div>
    renderWithRouter(<Dummy />)
    expect(screen.getByText('hello harness')).toBeInTheDocument()
  })
})
