import { describe, it, expect } from 'vitest'
import {
  PRIORITY_ORDER,
  PRIORITY_COLORS,
  PRIORITY_TEXT_COLORS,
} from './priority'

describe('PRIORITY_ORDER', () => {
  it('orders priorities from urgent (0) to low (3)', () => {
    expect(PRIORITY_ORDER).toEqual({ urgent: 0, high: 1, medium: 2, low: 3 })
  })

  it.each([
    ['urgent', 'high'],
    ['high', 'medium'],
    ['medium', 'low'],
  ])('ranks %s above %s', (higher, lower) => {
    expect(PRIORITY_ORDER[higher]).toBeLessThan(PRIORITY_ORDER[lower])
  })
})

describe('PRIORITY_COLORS', () => {
  it.each([
    ['urgent', 'bg-[var(--color-urgent)]'],
    ['high', 'bg-[var(--color-high)]'],
    ['medium', 'bg-[var(--color-medium)]'],
    ['low', 'bg-[var(--color-low)]'],
  ])('maps %s to a bg var class', (key, expected) => {
    expect(PRIORITY_COLORS[key]).toBe(expected)
  })
})

describe('PRIORITY_TEXT_COLORS', () => {
  it.each([
    ['urgent', 'text-[var(--color-urgent)]'],
    ['high', 'text-[var(--color-high)]'],
    ['medium', 'text-[var(--color-medium)]'],
    ['low', 'text-[var(--color-low)]'],
  ])('maps %s to a text var class', (key, expected) => {
    expect(PRIORITY_TEXT_COLORS[key]).toBe(expected)
  })
})
