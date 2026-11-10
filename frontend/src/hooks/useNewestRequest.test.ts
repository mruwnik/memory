import { describe, it, expect } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useNewestRequest } from './useNewestRequest'

describe('useNewestRequest', () => {
  it('marks a request stale once a newer one begins', () => {
    const { result } = renderHook(() => useNewestRequest())
    const first = result.current()
    expect(first()).toBe(false)
    const second = result.current()
    expect(first()).toBe(true)
    expect(second()).toBe(false)
  })

  it('keeps the same begin function across renders', () => {
    const { result, rerender } = renderHook(() => useNewestRequest())
    const begin = result.current
    rerender()
    expect(result.current).toBe(begin)
  })
})
