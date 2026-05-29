import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useDebounce } from './useDebounce'

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useDebounce', () => {
  it('returns the initial value immediately', () => {
    const { result } = renderHook(() => useDebounce('initial', 500))
    expect(result.current).toBe('initial')
  })

  it('does not update before the delay elapses', () => {
    const { result, rerender } = renderHook(
      ({ value, delay }) => useDebounce(value, delay),
      { initialProps: { value: 'a', delay: 500 } },
    )
    rerender({ value: 'b', delay: 500 })
    act(() => {
      vi.advanceTimersByTime(499)
    })
    expect(result.current).toBe('a')
  })

  it('updates to the latest value once the delay elapses', () => {
    const { result, rerender } = renderHook(
      ({ value, delay }) => useDebounce(value, delay),
      { initialProps: { value: 'a', delay: 500 } },
    )
    rerender({ value: 'b', delay: 500 })
    act(() => {
      vi.advanceTimersByTime(500)
    })
    expect(result.current).toBe('b')
  })

  it('resets the timer on rapid successive changes (only last value wins)', () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 500),
      { initialProps: { value: 'a' } },
    )
    rerender({ value: 'b' })
    act(() => {
      vi.advanceTimersByTime(300)
    })
    rerender({ value: 'c' })
    act(() => {
      vi.advanceTimersByTime(300)
    })
    // 600ms total elapsed, but only 300ms since last change -> still 'a'
    expect(result.current).toBe('a')
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(result.current).toBe('c')
  })

  it('works with non-string values (numbers)', () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 100),
      { initialProps: { value: 1 } },
    )
    rerender({ value: 42 })
    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(result.current).toBe(42)
  })

  it('respects a changed delay', () => {
    const { result, rerender } = renderHook(
      ({ value, delay }) => useDebounce(value, delay),
      { initialProps: { value: 'a', delay: 1000 } },
    )
    rerender({ value: 'b', delay: 100 })
    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(result.current).toBe('b')
  })
})
