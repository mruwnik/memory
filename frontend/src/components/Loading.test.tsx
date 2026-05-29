import { describe, it, expect } from 'vitest'
import { render, screen } from '@/test/utils'
import Loading from './Loading'

describe('Loading', () => {
  it('renders the default message when none is provided', () => {
    render(<Loading />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('renders a custom message', () => {
    render(<Loading message="Fetching data" />)
    expect(screen.getByText('Fetching data')).toBeInTheDocument()
  })

  it('renders the spinner element', () => {
    const { container } = render(<Loading />)
    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
  })
})
