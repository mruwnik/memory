import { describe, it, expect } from 'vitest'
import { formatCost } from './format'

describe('formatCost', () => {
  it.each([
    [0, '$0.00'],
    [0.000228, '<$0.01'],
    ['0.000228', '<$0.01'],
    [0.01, '$0.01'],
    [1.23456, '$1.23'],
    [12, '$12.00'],
  ])('formats %j as %s', (amount, expected) => {
    expect(formatCost(amount)).toBe(expected)
  })
})
