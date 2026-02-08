import { useEffect, useRef, useCallback, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const SCROLL_THROTTLE_MS = 100

// Map xterm.js key sequences to tmux send-keys format
function xtermToTmux(data: string): { keys: string; literal: boolean } {
  // Control characters
  if (data === '\x03') return { keys: 'C-c', literal: false }
  if (data === '\x04') return { keys: 'C-d', literal: false }
  if (data === '\x1a') return { keys: 'C-z', literal: false }
  if (data === '\x01') return { keys: 'C-a', literal: false }
  if (data === '\x05') return { keys: 'C-e', literal: false }
  if (data === '\x0b') return { keys: 'C-k', literal: false }
  if (data === '\x0c') return { keys: 'C-l', literal: false }
  if (data === '\x15') return { keys: 'C-u', literal: false }
  if (data === '\x17') return { keys: 'C-w', literal: false }

  // Arrow keys
  if (data === '\x1b[A') return { keys: 'Up', literal: false }
  if (data === '\x1b[B') return { keys: 'Down', literal: false }
  if (data === '\x1b[C') return { keys: 'Right', literal: false }
  if (data === '\x1b[D') return { keys: 'Left', literal: false }

  // Other special keys
  if (data === '\x7f') return { keys: 'BSpace', literal: false }
  if (data === '\x1b[3~') return { keys: 'DC', literal: false }
  if (data === '\t') return { keys: 'Tab', literal: false }
  if (data === '\r') return { keys: 'Enter', literal: false }
  if (data === '\x1b') return { keys: 'Escape', literal: false }

  // Home/End
  if (data === '\x1b[H' || data === '\x1bOH') return { keys: 'Home', literal: false }
  if (data === '\x1b[F' || data === '\x1bOF') return { keys: 'End', literal: false }

  // Page Up/Down
  if (data === '\x1b[5~') return { keys: 'PPage', literal: false }
  if (data === '\x1b[6~') return { keys: 'NPage', literal: false }

  // Alt+arrow (word navigation)
  if (data === '\x1b[1;3C') return { keys: 'M-Right', literal: false }
  if (data === '\x1b[1;3D') return { keys: 'M-Left', literal: false }
  if (data === '\x1bb') return { keys: 'M-b', literal: false }
  if (data === '\x1bf') return { keys: 'M-f', literal: false }

  // Printable characters - send literally
  return { keys: data, literal: true }
}

interface XtermTerminalProps {
  wsRef: React.RefObject<WebSocket | null>
  screenContent: string
  scrollOffset: number
  connected: boolean
}

export default function XtermTerminal({ wsRef, screenContent, scrollOffset, connected }: XtermTerminalProps) {
  const terminalRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<Terminal | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const lastScreenRef = useRef<string>('')
  const [error, setError] = useState<string | null>(null)

  // Send input to WebSocket
  const sendInput = useCallback((data: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return
    }
    const { keys, literal } = xtermToTmux(data)
    const payload = { type: 'input', keys, literal }
    wsRef.current.send(JSON.stringify(payload))
  }, [wsRef])

  // Send terminal size to backend for tmux resize
  const sendResize = useCallback((cols: number, rows: number) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return
    }
    const payload = { type: 'resize', cols, rows }
    wsRef.current.send(JSON.stringify(payload))
  }, [wsRef])

  // Initialize terminal.
  // Note: sendInput and sendResize are in the dependency array but are stable
  // (wrapped in useCallback with [wsRef] which is a stable ref). The terminal
  // initialization runs once and captures these callbacks in event handlers.
  useEffect(() => {
    if (!terminalRef.current || xtermRef.current) return

    try {
      const term = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        theme: {
          background: '#0f172a', // slate-900
          foreground: '#cbd5e1', // slate-300
          cursor: '#cbd5e1',
          cursorAccent: '#0f172a',
          selectionBackground: '#334155', // slate-700
        },
        convertEol: true,
        scrollback: 0,
      })

      const fitAddon = new FitAddon()
      term.loadAddon(fitAddon)

      term.open(terminalRef.current)
      fitAddon.fit()

      // Send initial size to backend
      sendResize(term.cols, term.rows)

      // Handle keyboard input
      term.onData((data) => {
        sendInput(data)
      })

      xtermRef.current = term
      fitAddonRef.current = fitAddon

      // Scroll handler - accumulate wheel delta during throttle window,
      // then send one batched scroll message with the total line count.
      let scrollAccumulator = 0
      let scrollTimer: ReturnType<typeof setTimeout> | null = null
      const flushScroll = () => {
        scrollTimer = null
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
          scrollAccumulator = 0
          return
        }
        if (scrollAccumulator === 0) return
        const direction = scrollAccumulator < 0 ? 'up' : 'down'
        // Each 100px of wheel delta ≈ 3 lines
        const lines = Math.max(1, Math.round(Math.abs(scrollAccumulator) / 30))
        wsRef.current.send(JSON.stringify({ type: 'scroll', direction, lines }))
        scrollAccumulator = 0
      }
      const handleWheel = (e: WheelEvent) => {
        e.preventDefault()
        e.stopPropagation()
        scrollAccumulator += e.deltaY
        if (!scrollTimer) {
          scrollTimer = setTimeout(flushScroll, SCROLL_THROTTLE_MS)
        }
      }
      // Use capture phase so we intercept before xterm.js's internal viewport
      terminalRef.current?.addEventListener('wheel', handleWheel, { passive: false, capture: true })

      // Resize handler - fit terminal to container and tell tmux to match
      const handleResize = () => {
        fitAddon.fit()
        sendResize(term.cols, term.rows)
      }
      window.addEventListener('resize', handleResize)

      // Clear any previous error on successful init
      setError(null)

      return () => {
        terminalRef.current?.removeEventListener('wheel', handleWheel, { capture: true })
        window.removeEventListener('resize', handleResize)
        term.dispose()
        xtermRef.current = null
        fitAddonRef.current = null
      }
    } catch (err) {
      console.error('Failed to initialize terminal:', err)
      setError(err instanceof Error ? err.message : 'Failed to initialize terminal')
    }
  }, [sendInput, sendResize])

  // Fit terminal when container might have resized, and send size on connect
  useEffect(() => {
    if (fitAddonRef.current && xtermRef.current) {
      // Small delay to ensure container has finished layout
      setTimeout(() => {
        fitAddonRef.current?.fit()
        if (connected && xtermRef.current) {
          sendResize(xtermRef.current.cols, xtermRef.current.rows)
        }
      }, 10)
    }
  }, [connected, sendResize])

  // Update terminal content when screen changes
  useEffect(() => {
    if (!xtermRef.current || !screenContent) return

    // Only update if content actually changed
    if (screenContent === lastScreenRef.current) return
    lastScreenRef.current = screenContent

    const term = xtermRef.current
    term.reset()
    term.write(screenContent)
  }, [screenContent])

  // Jump to bottom of scrollback — must be declared before the early return
  // so hooks are always called in the same order (Rules of Hooks).
  const jumpToBottom = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ type: 'scroll_to_bottom' }))
  }, [wsRef])

  // Show error fallback if terminal failed to initialize
  if (error) {
    return (
      <div
        className="w-full h-full min-h-[400px] flex items-center justify-center text-red-400"
        style={{ backgroundColor: '#0f172a' }}
      >
        <div className="text-center p-4">
          <p className="font-bold mb-2">Terminal initialization failed</p>
          <p className="text-sm text-slate-400">{error}</p>
          <p className="text-xs text-slate-500 mt-2">
            Try refreshing the page. If the problem persists, check browser compatibility.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="relative w-full h-full min-h-[400px]">
      <div
        ref={terminalRef}
        className="w-full h-full"
        style={{ backgroundColor: '#0f172a' }}
      />
      {scrollOffset > 0 && (
        <button
          onClick={jumpToBottom}
          aria-label="Jump to bottom of terminal"
          className={[
            "absolute bottom-4 right-4",
            "bg-slate-700/90 text-slate-200",
            "px-3 py-1.5 rounded-md text-xs font-mono",
            "hover:bg-slate-600 transition-colors",
          ].join(" ")}
        >
          Scrolled {scrollOffset} lines — click to jump to bottom
        </button>
      )}
    </div>
  )
}
