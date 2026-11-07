import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
import { Schedules } from './Schedules'

const listSchedules = vi.fn()
const saveSchedule = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listSchedules, saveSchedule, deleteSubscription: vi.fn() }),
}))

beforeEach(() => {
  listSchedules.mockReset().mockResolvedValue([])
  saveSchedule.mockReset()
})

describe('Schedules', () => {
  it.each([
    ['0 9 * * *', true],
    ['every day', false],
  ])('only schedules a five-field cron (%s)', async (cron, valid) => {
    const { user } = renderWithRouter(<Schedules definitionId={1} />)
    await screen.findByText('No schedules yet.')
    await user.clear(screen.getByLabelText('Cron (UTC)'))
    await user.type(screen.getByLabelText('Cron (UTC)'), cron)
    await user.type(screen.getByLabelText('Run input'), 'summarise')
    expect(screen.getByRole('button', { name: 'Schedule' }).hasAttribute('disabled')).toBe(!valid)
  })

  it('shows why a tripped schedule paused and resumes it by id', async () => {
    listSchedules.mockResolvedValue([{
      id: 5, kind: 'cron', name: 'schedule-5', config: { cron: '0 9 * * *', input: 'digest' }, next_at: null,
      enabled: false, state: { tripped: { reason: 'the API key was deleted', at: '2026-11-07T00:00:00Z' } },
    }])
    const { user } = renderWithRouter(<Schedules definitionId={1} />)
    expect(await screen.findByText('the API key was deleted')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Resume' }))
    expect(saveSchedule).toHaveBeenCalledWith({ definitionId: 1, enabled: true, scheduleId: 5 })
  })

  it('disables Schedule while saving', async () => {
    saveSchedule.mockReturnValue(new Promise(() => {}))
    const { user } = renderWithRouter(<Schedules definitionId={1} />)
    await screen.findByText('No schedules yet.')
    await user.type(screen.getByLabelText('Run input'), 'summarise')
    await user.click(screen.getByRole('button', { name: 'Schedule' }))
    expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled()
    expect(saveSchedule).toHaveBeenCalledTimes(1)
  })
})
