import { describe, it, expect, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import { renderWithRouter } from '@/test/utils'
import { AppShell } from './AppShell'

describe('AppShell section drawer', () => {
  it('keeps Tab inside the open drawer and returns focus to the toggle on Escape', async () => {
    const { user } = renderWithRouter(<AppShell user={null} onLogout={vi.fn()} hasScope={() => false} />, { initialEntries: ['/ui/dashboard'] })
    const toggle = screen.getByRole('button', { name: 'Sections' })
    await user.click(toggle)

    const drawer = screen.getByRole('dialog', { name: 'Sections' })
    const links = within(drawer).getAllByRole('link')
    expect(links[0]).toHaveFocus()
    await user.tab({ shift: true })
    expect(links[links.length - 1]).toHaveFocus()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(toggle).toHaveFocus()
  })
})
