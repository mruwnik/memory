import { describe, it, expect } from 'vitest'
import {
  PRIORITY_ORDER,
  dueLabel,
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

describe('dueLabel', () => {
  const now = new Date(2030, 5, 15, 12)
  it.each([
    [new Date(2030, 5, 14, 23).toISOString(), 'Overdue', 'danger'],
    [new Date(2030, 5, 15, 1).toISOString(), 'Today', 'warning'],
    [new Date(2030, 5, 16, 22).toISOString(), 'Tomorrow', 'info'],
    [new Date(2030, 5, 20, 9).toISOString(), 'Jun 20', 'neutral'],
  ])('labels %s as %s', (due, text, tone) => {
    expect(dueLabel(due, now)).toEqual({ text, tone })
  })
})
