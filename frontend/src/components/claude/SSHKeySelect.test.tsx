import { expect, it, vi } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import SSHKeySelect from './SSHKeySelect'
const list = vi.fn().mockResolvedValue([{ id: 2, name: 'Personal', user_id: 1, is_default: true }, { id: 9, name: 'Shared', user_id: null, is_default: false }])
vi.mock('../../hooks/useSSHKeys', () => ({ useSSHKeys: () => ({ list }) }))
it('offers default, explicit key, and no-key choices', async () => {
  const onChange = vi.fn()
  const { user } = renderWithUser(<SSHKeySelect value="" onChange={onChange} />)
  expect(await screen.findByRole('option', { name: 'Use my default (Personal)' })).toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('SSH key'), '9')
  expect(onChange).toHaveBeenLastCalledWith('9')
  await user.selectOptions(screen.getByLabelText('SSH key'), 'none')
  expect(onChange).toHaveBeenLastCalledWith('none')
})
