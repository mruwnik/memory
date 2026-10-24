import { describe, it, expect } from 'vitest'
import { apiError } from './apiError'
import { mockResponse } from '@/test/utils'

describe('apiError', () => {
  it.each([
    [{ detail: 'Slack app not found' }, 'Slack app not found'],
    [{ detail: [{ msg: 'field required' }, { msg: 'too long' }] }, 'field required; too long'],
    [{}, 'Failed to sync (503)'],
    [null, 'Failed to sync (503)'],
  ])('reads the reason from %j', async (json, expected) => {
    const error = await apiError(mockResponse({ status: 503, json }), 'Failed to sync')
    expect(error.message).toBe(expected)
  })

  it('falls back to the status when the body is not JSON', async () => {
    const response = { status: 502, json: () => Promise.reject(new SyntaxError('<html>')) } as unknown as Response
    expect((await apiError(response, 'Failed to sync')).message).toBe('Failed to sync (502)')
  })
})
