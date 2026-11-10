import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readStorage, readStoredArray, writeStorage } from './storage'

const blockStorage = () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
}

beforeEach(() => localStorage.clear())

describe('storage', () => {
  it('round-trips a value', () => {
    writeStorage('k', 'v')
    expect(readStorage('k')).toBe('v')
  })

  it('treats blocked storage as empty', () => {
    blockStorage()
    expect(() => writeStorage('k', 'v')).not.toThrow()
    expect(readStorage('k')).toBeNull()
    expect(readStoredArray('k')).toBeNull()
  })

  it.each([
    ['[1,2]', [1, 2]],
    ['not json{', null],
    ['{"a":1}', null],
  ])('reads %s as %j', (saved, expected) => {
    localStorage.setItem('k', saved)
    expect(readStoredArray('k')).toEqual(expected)
  })

  it('reads a missing key as null', () => {
    expect(readStoredArray('missing')).toBeNull()
  })
})
