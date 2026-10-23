// The app's page template and UI primitives. See frontend/STYLE.md for when to use which.
import { Children, cloneElement, isValidElement, useEffect, useId, useRef, useState, type ComponentProps, type KeyboardEvent as ReactKeyboardEvent, type ReactElement, type ReactNode } from 'react'
import { Link, type LinkProps } from 'react-router-dom'

export const cx = (...classes: (string | false | null | undefined)[]) => classes.filter(Boolean).join(' ')

// ---- Page template ----

const WIDTHS = { narrow: 'max-w-3xl', default: 'max-w-5xl', wide: 'max-w-7xl', full: '' }

interface PageProps {
  title: ReactNode
  back?: { to: string; label: string }
  description?: ReactNode
  actions?: ReactNode
  width?: keyof typeof WIDTHS
  /** Content fills the viewport below the header, for split-pane views that scroll internally. */
  fill?: boolean
  /** Blocking load state: replaces the content. Errors from actions on a loaded page belong in a Notice. */
  loading?: boolean | string
  error?: string | null
  onRetry?: () => void
  children?: ReactNode
}

export const Page = ({ title, back, description, actions, width = 'default', fill, loading, error, onRetry, children }: PageProps) => (
  <div className={cx('mx-auto w-full px-4 py-6 md:px-6', WIDTHS[width], fill && 'flex flex-col h-[calc(100dvh-3.5rem)]')}>
    {/* Actions sit on their own row under the title so long titles and many buttons never crowd each other. */}
    <header className="space-y-3 pb-4 mb-6 border-b border-slate-200">
      {back && (
        <Link to={back.to} className="inline-block text-sm text-slate-600 hover:text-primary">
          <span aria-hidden="true">← </span>{back.label}
        </Link>
      )}
      <div className="space-y-1">
        <h1 className="flex flex-wrap items-center gap-3 text-2xl font-semibold text-slate-800 break-words">{title}</h1>
        {description && <div className="text-sm text-slate-600">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
    <div className={fill ? 'flex-1 min-h-0' : 'space-y-6'}>
      {error ? <ErrorState message={error} onRetry={onRetry} />
        : loading ? <LoadingState message={typeof loading === 'string' ? loading : undefined} />
        : children}
    </div>
  </div>
)

interface SectionProps {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
}

export const Section = ({ title, description, actions, children, className }: SectionProps) => (
  <section className={cx('space-y-3', className)}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-lg font-semibold text-slate-800">{title}</h2>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
    {description && <div className="text-sm text-slate-600">{description}</div>}
    {children}
  </section>
)

// ---- Containers ----

interface CardProps extends Omit<ComponentProps<'div'>, 'title'> {
  title?: ReactNode
  actions?: ReactNode
  /** No padding, for cards whose content (tables, tab strips) runs edge to edge. */
  flush?: boolean
}

export const Card = ({ title, actions, flush, className, children, ...props }: CardProps) => (
  <div className={cx('bg-white rounded-xl border border-slate-200 shadow-sm', !flush && 'p-4 md:p-6', className)} {...props}>
    {(title || actions) && (
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        {title && <h2 className="text-lg font-semibold text-slate-800">{title}</h2>}
        {actions && <div className="flex flex-wrap items-center gap-2 ml-auto">{actions}</div>}
      </div>
    )}
    {children}
  </div>
)

export const List = ({ className, ...props }: ComponentProps<'ul'>) => (
  <ul className={cx('divide-y divide-slate-100 bg-white rounded-xl border border-slate-200 shadow-sm', className)} {...props} />
)

export const ListItem = ({ className, ...props }: ComponentProps<'li'>) => (
  <li className={cx('px-4 py-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-sm', className)} {...props} />
)

export const Table = ({ className, ...props }: ComponentProps<'table'>) => (
  <div className="overflow-x-auto bg-white rounded-xl border border-slate-200 shadow-sm">
    <table
      className={cx(
        'w-full text-sm text-left [&_th]:px-3 [&_th]:py-2 [&_th]:text-xs [&_th]:font-medium [&_th]:text-slate-600',
        '[&_thead]:border-b [&_thead]:border-slate-200 [&_td]:px-3 [&_td]:py-2 [&_tbody_tr+tr]:border-t [&_tbody_tr+tr]:border-slate-100',
        className,
      )}
      {...props}
    />
  </div>
)

// ---- Buttons ----

const BUTTON_VARIANTS = {
  primary: 'bg-primary text-white font-medium hover:bg-primary-dark',
  secondary: 'bg-white border border-slate-300 text-slate-700 hover:bg-slate-50',
  danger: 'bg-white border border-red-200 text-red-700 hover:bg-red-50',
  dangerSolid: 'bg-red-600 text-white font-medium hover:bg-red-700',
  ghost: 'text-slate-600 hover:bg-slate-100',
  link: 'text-primary hover:underline p-0!',
}
const BUTTON_SIZES = { md: 'py-2 px-4 text-sm', sm: 'py-1 px-2.5 text-xs', icon: 'w-9 h-9 text-lg' }
export type ButtonVariant = keyof typeof BUTTON_VARIANTS

export const buttonClass = (variant: ButtonVariant = 'secondary', size: keyof typeof BUTTON_SIZES = 'md', className?: string) => cx(
  'inline-flex items-center justify-center gap-2 rounded-lg transition-colors cursor-pointer',
  'disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
  BUTTON_SIZES[size], BUTTON_VARIANTS[variant], className,
)

interface ButtonProps extends ComponentProps<'button'> {
  variant?: ButtonVariant
  size?: keyof typeof BUTTON_SIZES
}

export const Button = ({ variant, size, className, ...props }: ButtonProps) => (
  <button className={buttonClass(variant, size, className)} {...props} />
)

export const ButtonLink = ({ variant, size, className, ...props }: LinkProps & Pick<ButtonProps, 'variant' | 'size'>) => (
  <Link className={buttonClass(variant, size, className)} {...props} />
)

// ---- Forms ----

const CONTROL = 'block py-2 px-3 bg-white border border-slate-300 rounded-lg text-sm text-slate-800 placeholder:text-slate-400 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:bg-slate-50 disabled:text-slate-500'
// Controls are full width unless the caller sizes them.
const controlClass = (className?: string) => cx(CONTROL, !/(^|\s)(w|flex)-/.test(className ?? '') && 'w-full', className)

export const Input = ({ className, ...props }: ComponentProps<'input'>) => (
  <input className={controlClass(className)} {...props} />
)

export const Select = ({ className, ...props }: ComponentProps<'select'>) => (
  <select className={controlClass(className)} {...props} />
)

export const Textarea = ({ className, ...props }: ComponentProps<'textarea'>) => (
  <textarea className={controlClass(cx('resize-y', className))} {...props} />
)

export const Label = ({ className, ...props }: ComponentProps<'label'>) => (
  <label className={cx('block text-sm font-medium text-slate-700', className)} {...props} />
)

export const Hint = ({ className, ...props }: ComponentProps<'p'>) => (
  <p className={cx('text-xs text-slate-600', className)} {...props} />
)

interface FieldProps extends ComponentProps<'div'> {
  label?: ReactNode
  hint?: ReactNode
  error?: string | null
}

/** A labelled control. With `label`, a single child control gets an id wired to the label. */
export const Field = ({ label, hint, error, className, children, ...props }: FieldProps) => {
  const generated = useId()
  const only = Children.count(children) === 1 && isValidElement(children) ? children as ReactElement<{ id?: string }> : null
  const id = only?.props.id ?? generated
  return (
    <div className={cx('space-y-1', className)} {...props}>
      {label && <Label htmlFor={only ? id : undefined}>{label}</Label>}
      {only && label && !only.props.id ? cloneElement(only, { id }) : children}
      {hint && <Hint>{hint}</Hint>}
      {error && <p className="text-xs text-red-700">{error}</p>}
    </div>
  )
}

export const FieldRow = ({ className, ...props }: ComponentProps<'div'>) => (
  <div className={cx('grid grid-cols-1 md:grid-cols-2 gap-4', className)} {...props} />
)

export const FormActions = ({ className, ...props }: ComponentProps<'div'>) => (
  <div className={cx('flex flex-wrap justify-end gap-2 pt-2', className)} {...props} />
)

export const Checkbox = ({ label, className, ...props }: ComponentProps<'input'> & { label: ReactNode }) => (
  <label className={cx('inline-flex items-center gap-2 text-sm text-slate-700', className)}>
    <input type="checkbox" {...props} />
    {label}
  </label>
)

export interface MultiSelectOption { value: string; label?: string; description?: string }

interface MultiSelectProps {
  options: (string | MultiSelectOption)[]
  value: string[]
  onChange: (value: string[]) => void
  id?: string
  placeholder?: string
  'aria-label'?: string
  /** Typed text that matches no option can be added as a value, as for free-form tags. */
  creatable?: boolean
  className?: string
}

/** Searchable multi-select: picked values show as removable chips, typing filters the remaining options. */
export const MultiSelect = ({ options, value, onChange, id, placeholder, creatable, className, ...props }: MultiSelectProps) => {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const close = (e: MouseEvent) => !rootRef.current?.contains(e.target as Node) && setOpen(false)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const opts = options.map(o => (typeof o === 'string' ? { value: o } : o))
  const labelOf = (v: string) => opts.find(o => o.value === v)?.label ?? v
  const typed = query.trim()
  const q = typed.toLowerCase()
  const matches: MultiSelectOption[] = opts.filter(o =>
    !value.includes(o.value) && [o.value, o.label, o.description].some(s => s?.toLowerCase().includes(q)))
  const canCreate = creatable && typed && !value.includes(typed) && !opts.some(o => o.value.toLowerCase() === q)
  const choices = canCreate ? [...matches, { value: typed, label: `Add "${typed}"` }] : matches
  const current = Math.min(active, choices.length - 1)

  const add = (v: string) => {
    onChange([...value, v])
    setQuery('')
    setActive(0)
  }
  const remove = (v: string) => onChange(value.filter(x => x !== v))

  const onKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setOpen(true)
      setActive(Math.max(0, Math.min(choices.length - 1, current + (e.key === 'ArrowDown' ? 1 : -1))))
    } else if (e.key === 'Enter' && open && choices[current]) {
      e.preventDefault()
      add(choices[current].value)
    } else if (e.key === 'Escape' && open) {
      // Close the list without also closing a surrounding Modal.
      e.stopPropagation()
      setOpen(false)
    } else if (e.key === 'Backspace' && !query && value.length) {
      remove(value[value.length - 1])
    }
  }

  return (
    <div ref={rootRef} className={cx('relative', className)}>
      <div onClick={() => inputRef.current?.focus()}
        className="flex flex-wrap items-center gap-1.5 w-full min-h-[2.375rem] py-1.5 px-2 bg-white border border-slate-300 rounded-lg text-sm cursor-text focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20">
        {value.map(v => (
          <span key={v} className="inline-flex items-center gap-1 max-w-full rounded bg-primary/10 text-primary-dark text-xs font-medium pl-2">
            <span className="truncate">{labelOf(v)}</span>
            <button type="button" aria-label={`Remove ${labelOf(v)}`} onClick={() => remove(v)}
              className="px-1.5 py-0.5 rounded hover:bg-primary/20 text-sm leading-none">&times;</button>
          </span>
        ))}
        <input ref={inputRef} id={id} role="combobox" aria-label={props['aria-label']} aria-expanded={open} aria-controls={listId}
          aria-autocomplete="list" aria-activedescendant={open && choices[current] ? `${listId}-${current}` : undefined}
          className="flex-1 min-w-[8rem] bg-transparent px-1 py-0.5 outline-none placeholder:text-slate-500"
          value={query} placeholder={value.length ? undefined : placeholder}
          onChange={e => { setQuery(e.target.value); setOpen(true); setActive(0) }}
          onFocus={() => setOpen(true)} onKeyDown={onKeyDown} />
      </div>
      {open && (
        <div className="absolute z-20 mt-1 w-full max-h-64 overflow-auto bg-white border border-slate-200 rounded-lg shadow-lg py-1">
          {choices.length === 0 && <p className="px-3 py-2 text-sm text-slate-500">{q ? 'No matches' : 'Nothing left to add'}</p>}
          <ul id={listId} role="listbox" aria-multiselectable="true">
            {choices.map((o, i) => (
              <li key={o.value} id={`${listId}-${i}`} role="option" aria-selected={i === current}
                onMouseDown={e => { e.preventDefault(); add(o.value) }} onMouseEnter={() => setActive(i)}
                className={cx('px-3 py-1.5 text-sm cursor-pointer', i === current && 'bg-slate-100')}>
                <div className="text-slate-800 break-all">{o.label ?? o.value}</div>
                {o.description && <div className="text-xs text-slate-500">{o.description}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ---- Status and feedback ----

const TONES = {
  neutral: 'bg-slate-100 text-slate-700',
  info: 'bg-blue-100 text-blue-800',
  success: 'bg-green-100 text-green-800',
  warning: 'bg-amber-100 text-amber-800',
  danger: 'bg-red-100 text-red-800',
  primary: 'bg-primary/10 text-primary-dark',
}
export type Tone = keyof typeof TONES

export const Badge = ({ tone = 'neutral', className, ...props }: ComponentProps<'span'> & { tone?: Tone }) => (
  <span className={cx('inline-flex items-center px-2 py-0.5 rounded text-xs font-medium whitespace-nowrap', TONES[tone], className)} {...props} />
)

const NOTICE_TONES = {
  error: 'bg-red-50 border-red-200 text-red-800',
  warning: 'bg-amber-50 border-amber-200 text-amber-800',
  info: 'bg-blue-50 border-blue-200 text-blue-800',
  success: 'bg-green-50 border-green-200 text-green-800',
}

interface NoticeProps extends ComponentProps<'div'> {
  tone: keyof typeof NOTICE_TONES
  onRetry?: () => void
  onDismiss?: () => void
}

export const Notice = ({ tone, onRetry, onDismiss, className, children, ...props }: NoticeProps) => (
  <div role={tone === 'error' || tone === 'warning' ? 'alert' : 'status'}
    className={cx('p-3 border rounded-lg text-sm whitespace-pre-wrap flex items-start justify-between gap-3', NOTICE_TONES[tone], className)} {...props}>
    <div className="min-w-0">{children}</div>
    {onRetry && <button type="button" className="shrink-0 underline hover:no-underline" onClick={onRetry}>Retry</button>}
    {onDismiss && <button type="button" className="shrink-0 underline hover:no-underline" onClick={onDismiss}>Dismiss</button>}
  </div>
)

interface EmptyStateProps {
  message: ReactNode
  actionLabel?: string
  onAction?: () => void
}

export const EmptyState = ({ message, actionLabel, onAction }: EmptyStateProps) => (
  <div className="rounded-xl border border-dashed border-slate-300 py-10 px-4 text-center text-slate-600">
    <div>{message}</div>
    {actionLabel && onAction && <Button variant="primary" className="mt-4" onClick={onAction}>{actionLabel}</Button>}
  </div>
)

export const LoadingState = ({ message = 'Loading...' }: { message?: string }) => (
  <div role="status" className="flex flex-col items-center justify-center py-12 text-slate-600">
    <div className="w-6 h-6 border-2 border-slate-200 border-t-primary rounded-full animate-spin motion-reduce:animate-none mb-2" />
    <p>{message}</p>
  </div>
)

export const ErrorState = ({ message, onRetry }: { message: string; onRetry?: () => void }) => (
  <div role="alert" className="text-center py-12 text-red-700">
    <p className="mb-4">{message}</p>
    {onRetry && <Button variant="primary" onClick={onRetry}>Retry</Button>}
  </div>
)

// ---- Choice controls ----

interface Option<T extends string> { value: T; label: ReactNode }

/** Underlined tabs that switch the view below them. */
export function Tabs<T extends string>({ options, value, onChange, label }: { options: Option<T>[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 border-b border-slate-200">
      {options.map(o => (
        <button key={o.value} type="button" role="tab" aria-selected={o.value === value} onClick={() => onChange(o.value)}
          className={cx('py-2 px-4 -mb-px text-sm font-medium border-b-2 transition-colors',
            o.value === value ? 'border-primary text-primary' : 'border-transparent text-slate-600 hover:text-slate-800 hover:border-slate-300')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** A row of mutually exclusive pills, for filters and ranges. */
export function ToggleGroup<T extends string>({ options, value, onChange, label }: { options: Option<T>[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1">
      {options.map(o => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}
          className={cx('px-3 py-1 rounded-full text-xs font-medium border transition-colors',
            o.value === value ? 'bg-primary border-primary text-white' : 'bg-white border-slate-300 text-slate-700 hover:bg-slate-50')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ---- Dialogs ----

function useEscape(onEscape: () => void) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onEscape()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onEscape])
}

const MODAL_SIZES = { sm: 'max-w-sm', md: 'max-w-md', lg: 'max-w-lg', xl: 'max-w-2xl' }

interface ModalProps {
  title: ReactNode
  onClose: () => void
  children: ReactNode
  size?: keyof typeof MODAL_SIZES
  className?: string
}

const FOCUSABLE = 'a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'

export const Modal = ({ title, onClose, children, size = 'lg', className }: ModalProps) => {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  useEscape(onClose)

  // Focus the first control on open, keep Tab inside the dialog, and restore focus on close.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null
    const dialog = dialogRef.current
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])
    ;(bodyRef.current?.querySelector<HTMLElement>(FOCUSABLE) ?? focusable()[0])?.focus()
    const trapTab = (e: KeyboardEvent) => {
      const items = focusable()
      if (e.key !== 'Tab' || !items.length) return
      const [first, last] = [items[0], items[items.length - 1]]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    dialog?.addEventListener('keydown', trapTab)
    return () => {
      dialog?.removeEventListener('keydown', trapTab)
      previouslyFocused?.focus()
    }
  }, [])

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId}
        className={cx('bg-white rounded-xl shadow-xl w-full max-h-[85vh] overflow-auto', MODAL_SIZES[size], className)}
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-slate-100">
          <h2 id={titleId} className="text-lg font-semibold text-slate-800">{title}</h2>
          <button aria-label="Close" className="text-slate-500 hover:text-slate-700 text-2xl leading-none" onClick={onClose}>&times;</button>
        </div>
        <div ref={bodyRef} className="p-4">{children}</div>
      </div>
    </div>
  )
}

interface ConfirmDialogProps {
  message: string
  onConfirm: () => void
  onCancel: () => void
  title?: string
  confirmLabel?: string
}

export const ConfirmDialog = ({ message, onConfirm, onCancel, title, confirmLabel = 'Confirm' }: ConfirmDialogProps) => {
  const titleId = useId()
  const cancelRef = useRef<HTMLButtonElement>(null)
  useEscape(onCancel)
  // Cancel is the safer default focus for destructive confirmations
  useEffect(() => cancelRef.current?.focus(), [])

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="bg-white rounded-xl shadow-xl p-6 max-w-sm w-full">
        {title && <h2 id={titleId} className="text-lg font-semibold text-slate-900 mb-2">{title}</h2>}
        <p id={title ? undefined : titleId} className="text-slate-800 mb-4">{message}</p>
        <div className="flex justify-end gap-2">
          <button ref={cancelRef} className={buttonClass('secondary')} onClick={onCancel}>Cancel</button>
          <Button variant="dangerSolid" onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  )
}
