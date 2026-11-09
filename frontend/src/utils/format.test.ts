import { describe, it, expect } from 'vitest'
import { formatBytes, formatCost } from './format'

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

describe('formatBytes', () => {
  it.each([
    [0, '0 B'],
    [1023, '1023 B'],
    [1024, '1.0 KB'],
    [1536, '1.5 KB'],
    [5 * 1024 ** 2, '5.0 MB'],
    [3 * 1024 ** 5, '3072.0 TB'],
  ])('formats %d as %s', (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected)
  })
})
