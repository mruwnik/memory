import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createRef } from 'react'
import { render, screen, waitFor } from '@/test/utils'
import userEvent from '@testing-library/user-event'

// xterm and its fit addon don't run in jsdom — mock with no-op classes that
// record the data/resize handlers so we can drive them.
const onDataHandlers: Array<(d: string) => void> = []
const writeMock = vi.fn()
const resetMock = vi.fn()
const disposeMock = vi.fn()

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = 80
    rows = 24
    loadAddon = vi.fn()
    open = vi.fn()
    write = writeMock
    reset = resetMock
    dispose = disposeMock
    onData = (cb: (d: string) => void) => { onDataHandlers.push(cb) }
  },
}))
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    fit = vi.fn()
  },
}))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))

import XtermTerminal from './XtermTerminal'

const makeWs = (readyState: number = WebSocket.OPEN) => {
  const send = vi.fn()
  const ref = createRef<WebSocket | null>() as React.MutableRefObject<WebSocket | null>
  ref.current = { readyState, send, close: vi.fn() } as unknown as WebSocket
  return { ref, send }
}

beforeEach(() => {
  onDataHandlers.length = 0
  writeMock.mockClear()
  resetMock.mockClear()
  disposeMock.mockClear()
})

describe('XtermTerminal', () => {
  it('renders a terminal container', () => {
    const { ref } = makeWs()
    const { container } = render(
      <XtermTerminal wsRef={ref} screenContent="" scrollOffset={0} connected={false} />,
    )
    expect(container.querySelector('div.relative')).not.toBeNull()
  })

  it('writes screen content to the terminal when it changes', async () => {
    const { ref } = makeWs()
    const { rerender } = render(
      <XtermTerminal wsRef={ref} screenContent="" scrollOffset={0} connected />,
    )
    rerender(<XtermTerminal wsRef={ref} screenContent="hello world" scrollOffset={0} connected />)
    await waitFor(() => expect(writeMock).toHaveBeenCalledWith('hello world'))
    expect(resetMock).toHaveBeenCalled()
  })

  it('does not re-write identical screen content', async () => {
    const { ref } = makeWs()
    const { rerender } = render(
      <XtermTerminal wsRef={ref} screenContent="same" scrollOffset={0} connected />,
    )
    await waitFor(() => expect(writeMock).toHaveBeenCalledTimes(1))
    rerender(<XtermTerminal wsRef={ref} screenContent="same" scrollOffset={0} connected />)
    expect(writeMock).toHaveBeenCalledTimes(1)
  })

  it('sends keyboard input to the websocket as tmux send-keys (control char)', async () => {
    const { ref, send } = makeWs()
    render(<XtermTerminal wsRef={ref} screenContent="" scrollOffset={0} connected />)
    await waitFor(() => expect(onDataHandlers.length).toBeGreaterThan(0))
    onDataHandlers[0]('\x03') // Ctrl-C
    const payload = JSON.parse(send.mock.calls.find(c => String(c[0]).includes('input'))![0])
    expect(payload).toMatchObject({ type: 'input', keys: 'C-c', literal: false })
  })

  it('sends printable characters literally', async () => {
    const { ref, send } = makeWs()
    render(<XtermTerminal wsRef={ref} screenContent="" scrollOffset={0} connected />)
    await waitFor(() => expect(onDataHandlers.length).toBeGreaterThan(0))
    send.mockClear()
    onDataHandlers[0]('a')
    const payload = JSON.parse(send.mock.calls.find(c => String(c[0]).includes('input'))![0])
    expect(payload).toMatchObject({ type: 'input', keys: 'a', literal: true })
  })

  it('does not send input when the websocket is not open', async () => {
    const { ref, send } = makeWs(WebSocket.CLOSED)
    render(<XtermTerminal wsRef={ref} screenContent="" scrollOffset={0} connected={false} />)
    await waitFor(() => expect(onDataHandlers.length).toBeGreaterThan(0))
    send.mockClear()
    onDataHandlers[0]('x')
    expect(send).not.toHaveBeenCalled()
  })

  it('shows the scroll-to-bottom button only when scrolled, and sends scroll_to_bottom', async () => {
    const { ref, send } = makeWs()
    const { rerender } = render(
      <XtermTerminal wsRef={ref} screenContent="" scrollOffset={0} connected />,
    )
    expect(screen.queryByRole('button', { name: /Jump to bottom/ })).not.toBeInTheDocument()
    rerender(<XtermTerminal wsRef={ref} screenContent="" scrollOffset={42} connected />)
    const btn = screen.getByRole('button', { name: 'Jump to bottom of terminal' })
    expect(btn).toHaveTextContent('Scrolled 42 lines')
    await userEvent.setup().click(btn)
    const payload = JSON.parse(send.mock.calls.find(c => String(c[0]).includes('scroll_to_bottom'))![0])
    expect(payload).toEqual({ type: 'scroll_to_bottom' })
  })
})
