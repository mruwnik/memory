import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import TimezoneSelect from './TimezoneSelect'
import * as tzUtils from '../../utils/timezones'

describe('TimezoneSelect', () => {
  beforeEach(() => {
    vi.spyOn(tzUtils, 'getBrowserTimezone').mockReturnValue('UTC')
  })

  it('renders the default label and uses the provided id', () => {
    renderWithUser(<TimezoneSelect value="UTC" onChange={() => {}} />)
    expect(screen.getByLabelText('Timezone')).toBeInTheDocument()
  })

  it('renders a custom label and id', () => {
    renderWithUser(
      <TimezoneSelect value="UTC" onChange={() => {}} id="tz2" label="Zone" />,
    )
    const select = screen.getByLabelText('Zone') as HTMLSelectElement
    expect(select.id).toBe('tz2')
  })

  it('reflects the controlled value', () => {
    renderWithUser(<TimezoneSelect value="Asia/Tokyo" onChange={() => {}} />)
    const select = screen.getByLabelText('Timezone') as HTMLSelectElement
    expect(select.value).toBe('Asia/Tokyo')
  })

  it('calls onChange with the selected timezone', async () => {
    const onChange = vi.fn()
    const { user } = renderWithUser(
      <TimezoneSelect value="UTC" onChange={onChange} />,
    )
    await user.selectOptions(screen.getByLabelText('Timezone'), 'Asia/Tokyo')
    expect(onChange).toHaveBeenCalledWith('Asia/Tokyo')
  })

  it('does NOT prepend the browser tz when it is already a common timezone', () => {
    renderWithUser(<TimezoneSelect value="UTC" onChange={() => {}} />)
    const options = screen.getAllByRole('option') as HTMLOptionElement[]
    const utcOptions = options.filter(o => o.value === 'UTC')
    expect(utcOptions).toHaveLength(1)
  })

  it('prepends the browser tz when it is not in the common list', () => {
    vi.spyOn(tzUtils, 'getBrowserTimezone').mockReturnValue('America/Lima')
    renderWithUser(<TimezoneSelect value="America/Lima" onChange={() => {}} />)
    const options = screen.getAllByRole('option') as HTMLOptionElement[]
    expect(options.some(o => o.value === 'America/Lima')).toBe(true)
  })

  it('applies the provided className to the wrapper', () => {
    const { container } = renderWithUser(
      <TimezoneSelect value="UTC" onChange={() => {}} className="my-wrap" />,
    )
    expect(container.querySelector('.my-wrap')).toBeInTheDocument()
  })
})
