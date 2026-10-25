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

  it.each([
    [{ detail: [{ loc: ['body', 'url'], msg: 'URL is not allowed' }, { loc: ['body', 'tags', 0], msg: 'too long' }, { msg: 'no loc' }] },
      { url: 'URL is not allowed', 'tags.0': 'too long' }],
    [{ detail: [{ loc: ['body', 'email'], msg: 'taken' }, { loc: ['body', 'email'], msg: 'also bad' }] }, { email: 'taken; also bad' }],
    [{ detail: 'Slack app not found' }, {}],
  ])('keeps each reason under the field it names, from %j', async (json, fields) => {
    const error = await apiError(mockResponse({ status: 400, json }), 'Failed to save')
    expect(error.fields).toEqual(fields)
  })

  it('falls back to the status when the body is not JSON', async () => {
    const response = { status: 502, json: () => Promise.reject(new SyntaxError('<html>')) } as unknown as Response
    expect((await apiError(response, 'Failed to sync')).message).toBe('Failed to sync (502)')
  })
})
