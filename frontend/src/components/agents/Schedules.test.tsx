import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
import { Schedules } from './Schedules'

const listSchedules = vi.fn()
const saveSchedule = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listSchedules, saveSchedule, deleteSchedule: vi.fn() }),
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
