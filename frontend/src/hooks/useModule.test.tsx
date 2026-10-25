import { describe, it, expect, vi } from 'vitest'
import { render, renderHook } from '@testing-library/react'
import { useModule } from './useModule'
import { modulesWrapper } from '@/test/utils'

describe('useModule', () => {
  it.each([
    ['enabled', new Set(['teams']), true],
    ['disabled', new Set<string>(), false],
  ])('reports a %s module', (_state, modules, expected) => {
    const { result } = renderHook(() => useModule('teams'), { wrapper: modulesWrapper(modules) })
    expect(result.current).toBe(expected)
  })

  it('throws without a provider rather than running with every module off', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const Probe = () => <>{String(useModule('teams'))}</>
    expect(() => render(<Probe />)).toThrow('ModulesContext')
  })
})
