import { useEffect, useMemo, useRef, useState } from 'react'

export interface ComboOption {
  value: string
  label: string
  group?: string
}

interface ComboboxProps {
  value: string
  /** option is undefined when the value is free-typed (custom) rather than picked. */
  onChange: (value: string, option?: ComboOption) => void
  options: ComboOption[]
  placeholder?: string
  /** Keep a free-typed value that matches no option (defaults to false). */
  allowCustom?: boolean
  loading?: boolean
  /** Provide for async search; when set, options are shown unfiltered (the parent filters). */
  onQueryChange?: (query: string) => void
  disabled?: boolean
  className?: string
}

const inputClass =
  'w-full px-3 py-1.5 border border-slate-200 rounded text-sm focus:outline-none focus:ring-1 focus:ring-primary'

function labelForValue(value: string, options: ComboOption[]): string {
  return options.find(o => o.value === value)?.label ?? value
}

const Combobox = ({
  value,
  onChange,
  options,
  placeholder,
  allowCustom = false,
  loading = false,
  onQueryChange,
  disabled = false,
  className,
}: ComboboxProps) => {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [highlight, setHighlight] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)

  // Reflect the externally-selected value as the field text while closed.
  useEffect(() => {
    if (!open) setQuery(value ? labelForValue(value, options) : '')
  }, [value, options, open])

  // Close on outside click.
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const filtered = useMemo(() => {
    if (onQueryChange) return options // async: parent already filtered
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter(
      o => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)
    )
  }, [options, query, onQueryChange])

  const commitCustom = () => {
    const text = query.trim()
    const match = options.find(o => o.label === query || o.value === text)
    if (match) {
      onChange(match.value, match)
    } else if (allowCustom && text) {
      onChange(text, undefined)
    }
    // Close so the value-sync effect re-runs and snaps the field text back to
    // the committed value's label (prevents text/value divergence on blur).
    setOpen(false)
  }

  const selectOption = (opt: ComboOption) => {
    onChange(opt.value, opt)
    setQuery(opt.label)
    setOpen(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) {
        // Opening from closed: land on the first option, don't skip it.
        setOpen(true)
        setHighlight(0)
      } else {
        setHighlight(h => Math.min(h + 1, filtered.length - 1))
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight(h => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (open && filtered[highlight]) {
        selectOption(filtered[highlight])
      } else {
        commitCustom()
        setOpen(false)
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  // Render options with optional group headers.
  let lastGroup: string | undefined
  const rows: React.ReactNode[] = []
  filtered.forEach((opt, i) => {
    if (opt.group && opt.group !== lastGroup) {
      lastGroup = opt.group
      rows.push(
        <li
          key={`group-${opt.group}`}
          className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400"
        >
          {opt.group}
        </li>
      )
    }
    rows.push(
      <li
        key={`${opt.value}-${i}`}
        onMouseDown={e => {
          e.preventDefault()
          selectOption(opt)
        }}
        onMouseEnter={() => setHighlight(i)}
        className={`px-3 py-1.5 text-sm cursor-pointer ${
          i === highlight ? 'bg-primary/10 text-primary' : 'text-slate-700 hover:bg-slate-50'
        }`}
      >
        {opt.label}
      </li>
    )
  })

  return (
    <div ref={containerRef} className={`relative ${className ?? ''}`}>
      <input
        type="text"
        value={query}
        disabled={disabled}
        placeholder={placeholder}
        className={inputClass}
        onChange={e => {
          setQuery(e.target.value)
          setOpen(true)
          setHighlight(0)
          onQueryChange?.(e.target.value)
        }}
        onFocus={() => setOpen(true)}
        onBlur={commitCustom}
        onKeyDown={handleKeyDown}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
      />
      {open && (
        <ul className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded border border-slate-200 bg-white shadow-lg">
          {loading && <li className="px-3 py-2 text-sm text-slate-400">Loading…</li>}
          {!loading && filtered.length === 0 && (
            <li className="px-3 py-2 text-sm text-slate-400">
              {allowCustom ? 'No matches — press Enter to use as-is' : 'No matches'}
            </li>
          )}
          {!loading && rows}
        </ul>
      )}
    </div>
  )
}

export default Combobox
