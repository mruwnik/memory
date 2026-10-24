// The app's page template and UI primitives. See frontend/STYLE.md for when to use which.
import { Children, Fragment, cloneElement, isValidElement, useCallback, useEffect, useId, useRef, useState, type ComponentProps, type KeyboardEvent as ReactKeyboardEvent, type ReactElement, type ReactNode, type RefObject } from 'react'
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
  description?: ReactNode
  actions?: ReactNode
  /** No padding, for cards whose content (tables, tab strips) runs edge to edge. */
  flush?: boolean
}

export const Card = ({ title, description, actions, flush, className, children, ...props }: CardProps) => (
  <div className={cx('bg-white rounded-xl border border-slate-200 shadow-sm', !flush && 'p-4 md:p-6', className)} {...props}>
    {(title || actions) && (
      <div className={cx('flex flex-wrap items-center justify-between gap-2', flush ? 'px-4 py-3' : 'mb-4')}>
        {title && (
          <div className="space-y-1">
            <h2 className="text-lg font-semibold text-slate-800">{title}</h2>
            {description && <div className="text-sm text-slate-600">{description}</div>}
          </div>
        )}
        {actions && <div className="flex flex-wrap items-center gap-2 ml-auto">{actions}</div>}
      </div>
    )}
    {children}
  </div>
)

// ---- Collections ----

interface ToolbarProps {
  children?: ReactNode
  /** Right-aligned: refresh and bulk actions. A page's create action goes in Page `actions`, not here. */
  actions?: ReactNode
  /** Number of selected rows; when non-zero the toolbar shows it with a Clear button before the actions. */
  selected?: number
  onClearSelection?: () => void
}

/** Search and filters for the collection below it. Sits directly under the page header, never inside a Card. */
export const Toolbar = ({ children, actions, selected, onClearSelection }: ToolbarProps) => (
  <div className="flex flex-wrap items-center gap-2">
    {children}
    <div className="flex flex-wrap items-center gap-2 ml-auto">
      {!!selected && (
        <>
          <span className="text-sm text-slate-600">{selected} selected</span>
          {onClearSelection && <Button variant="ghost" size="sm" onClick={onClearSelection}>Clear</Button>}
        </>
      )}
      {actions}
    </div>
  </div>
)

export const RefreshButton = ({ onClick, loading, label = 'Refresh' }: { onClick: () => void; loading?: boolean; label?: string }) => (
  <Button size="icon" onClick={onClick} disabled={loading} title={label} aria-label={label}>
    <span aria-hidden="true" className={cx(loading && 'animate-spin motion-reduce:animate-none')}>&#8635;</span>
  </Button>
)

export const List = ({ flush, className, ...props }: ComponentProps<'ul'> & { flush?: boolean }) => (
  <ul className={cx('divide-y divide-slate-100', !flush && 'bg-white rounded-xl border border-slate-200 shadow-sm', className)} {...props} />
)

interface RowProps {
  title: ReactNode
  /** The title links here, and the whole row header is clickable. */
  to?: string
  /** The title is a button, and the whole row header is clickable: selects the item or toggles its detail. */
  onSelect?: () => void
  /** Highlight the row as the one shown in the detail pane. */
  selected?: boolean
  /** With `onSelect`: the row toggles inline detail (its children), announced via aria-expanded. */
  expanded?: boolean
  /** Before the content: a selection checkbox or an avatar. */
  leading?: ReactNode
  badges?: ReactNode
  /** Short facts (time, counts, owner) joined with a middle dot; falsy entries are dropped. */
  meta?: ReactNode[]
  /** Trailing buttons; on phones they sit under the meta line. */
  actions?: ReactNode
  /** Archived, inactive or closed items: the title is greyed; say why with a badge. */
  muted?: boolean
  /** Description, errors, or expanded detail, under the header. */
  children?: ReactNode
  className?: string
}

const STRETCH = 'text-left focus-visible:outline-none after:absolute after:inset-0 after:rounded focus-visible:after:outline-2 focus-visible:after:outline-primary'

/** One item in a `List`: title, badges, meta line, trailing actions, then optional body. */
export const Row = ({ title, to, onSelect, selected, expanded, leading, badges, meta, actions, muted, children, className }: RowProps) => {
  const facts = meta?.filter(m => m !== null && m !== undefined && m !== false && m !== '')
  const titleClass = cx('font-medium break-words', muted ? 'text-slate-500' : 'text-slate-800', (to || onSelect) && STRETCH)
  return (
    <li className={cx('flex gap-3 px-4 py-3 text-sm', selected ? 'bg-primary/10' : (to || onSelect) && 'hover:bg-slate-50', className)}>
      {leading && <div className="shrink-0 pt-0.5">{leading}</div>}
      <div className="flex-1 min-w-0 space-y-2">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-x-4 gap-y-2">
          <div className="relative flex-1 min-w-0 space-y-0.5">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              {to ? <Link to={to} className={titleClass} aria-current={selected || undefined}>{title}</Link>
                : onSelect ? <button type="button" onClick={onSelect} className={titleClass} aria-current={selected || undefined} aria-expanded={expanded}>{title}</button>
                : <span className={titleClass}>{title}</span>}
              {badges}
            </div>
            {!!facts?.length && (
              <div className="flex flex-wrap gap-x-1.5 text-xs text-slate-500">
                {facts.map((m, i) => <span key={i}>{i > 0 && <span aria-hidden="true" className="mr-1.5">·</span>}{m}</span>)}
              </div>
            )}
          </div>
          {actions && <div className="relative flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
        {children}
      </div>
    </li>
  )
}

/** An item's error inside a Row: the failure reason of a job, sync or run. */
export const RowError = ({ children }: { children: ReactNode }) => (
  <p className="text-sm text-red-700 whitespace-pre-wrap break-words">{children}</p>
)

/** Previous/next paging under a List, for collections fetched a page at a time. */
export const Pager = ({ page, hasMore, onPage }: { page: number; hasMore: boolean; onPage: (page: number) => void }) => (
  <nav aria-label="Pagination" className="flex items-center justify-between gap-2">
    <Button onClick={() => onPage(Math.max(0, page - 1))} disabled={page === 0}>Previous</Button>
    <span className="text-sm text-slate-600">Page {page + 1}{hasMore ? '' : ' (last)'}</span>
    <Button onClick={() => onPage(page + 1)} disabled={!hasMore}>Next</Button>
  </nav>
)

// ---- Details and stats ----

/** Label/value facts about one item. Entries with an empty value are dropped. */
export const DetailList = ({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) => (
  <dl className={cx('grid grid-cols-1 sm:grid-cols-[minmax(7rem,max-content)_1fr] gap-x-6 gap-y-1 sm:gap-y-2 text-sm', className)}>
    {items.filter(([, v]) => v !== null && v !== undefined && v !== false && v !== '').map(([label, value], i) => (
      <div key={i} className="contents">
        <dt className="text-slate-500 pt-1 sm:pt-0">{label}</dt>
        <dd className="text-slate-800 min-w-0 break-words">{value}</dd>
      </div>
    ))}
  </dl>
)

const STAT_TONES = { neutral: 'text-slate-800', success: 'text-green-700', warning: 'text-amber-700', danger: 'text-red-700', info: 'text-blue-700' }

// Columns per tile count, so rows fill evenly instead of leaving one tile on its own line
const STAT_COLUMNS = ['grid-cols-1', 'grid-cols-1', 'grid-cols-2', 'grid-cols-3', 'grid-cols-2 lg:grid-cols-4', 'grid-cols-2 lg:grid-cols-5', 'grid-cols-2 sm:grid-cols-3 xl:grid-cols-6']

/** Headline numbers at the top of a dashboard-like page. Children are `Stat`s. */
export const StatGrid = ({ children }: { children: ReactNode }) => (
  <dl className={cx('grid gap-3', STAT_COLUMNS[Math.min(Children.toArray(children).length, 6)])}>{children}</dl>
)

export const Stat = ({ label, value, hint, tone = 'neutral' }: { label: ReactNode; value: ReactNode; hint?: ReactNode; tone?: keyof typeof STAT_TONES }) => (
  <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 min-w-0">
    <dt className="text-xs font-medium text-slate-600">{label}</dt>
    <dd className={cx('mt-1 text-2xl font-semibold tabular-nums break-words', STAT_TONES[tone])}>{value}</dd>
    {hint && <dd className="mt-0.5 text-xs text-slate-500">{hint}</dd>}
  </div>
)

/** Dashboard panels (charts, recent-activity tables) in two columns on wide screens. Children are Cards; `lg:col-span-2` widens one. */
export const CardGrid = ({ children }: { children: ReactNode }) => (
  <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">{children}</div>
)

/** A collapsible block for secondary detail (commands, history, advanced options). */
export const Disclosure = ({ summary, children, className, ...props }: Omit<ComponentProps<'details'>, 'title'> & { summary: ReactNode }) => (
  <details className={cx('group bg-white rounded-xl border border-slate-200', className)} {...props}>
    <summary className="flex items-center gap-2 px-4 py-3 cursor-pointer text-sm font-medium text-slate-700 hover:bg-slate-50 rounded-xl">
      <span aria-hidden="true" className="text-slate-500 transition-transform group-open:rotate-90 motion-reduce:transition-none">▸</span>
      {summary}
    </summary>
    <div className="px-4 pb-4">{children}</div>
  </details>
)

interface SubListProps {
  /** Heading of the nested collection; an ExpandToggle when it can be collapsed. */
  title: ReactNode
  actions?: ReactNode
  /** Shown instead of the list when there are no rows. */
  empty?: ReactNode
  /** `Row`s. */
  children?: ReactNode
  /** Below the rows, e.g. a form that adds one. */
  footer?: ReactNode
  /** Scroll the rows past a fixed height, for collections that grow without bound. */
  scroll?: boolean
}

/** A collection nested inside a Row: an item's repos, folders, servers or run history. */
export const SubList = ({ title, actions, empty, children, footer, scroll }: SubListProps) => (
  <div className="border-t border-slate-100 pt-2 space-y-1">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-medium text-slate-700">{title}</h3>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
    {Children.toArray(children).length > 0
      ? <List flush className={cx('[&>li]:px-0', scroll && 'max-h-48 overflow-y-auto')}>{children}</List>
      : empty && <Hint>{empty}</Hint>}
    {footer}
  </div>
)

/** Shows or hides a nested collection under a Row (servers of a bot, journal of a project). */
export const ExpandToggle = ({ expanded, onClick, children }: { expanded: boolean; onClick: () => void; children: ReactNode }) => (
  <button type="button" aria-expanded={expanded} onClick={onClick}
    className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-700 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-primary rounded">
    <span aria-hidden="true" className={cx('text-slate-500 transition-transform motion-reduce:transition-none', expanded && 'rotate-90')}>▸</span>
    {children}
  </button>
)

// ---- Master-detail ----

/** Title bar of a pane in a SplitView: the list's heading, or the selected item's title and actions. */
export const PaneHeader = ({ title, actions }: { title: ReactNode; actions?: ReactNode }) => (
  <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-slate-200">
    <h2 className="text-sm font-semibold text-slate-700 min-w-0 break-words">{title}</h2>
    {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
  </div>
)

interface SplitViewProps {
  /** Shown above the list as a PaneHeader. */
  listTitle?: ReactNode
  /** Usually a flush `List` of `Row`s with `onSelect`/`selected`. */
  list: ReactNode
  /** The selected item, or null. */
  detail: ReactNode
  /** What the detail pane shows when nothing is selected; below `md` it sits under the list. */
  placeholder?: ReactNode
  /** Narrow screens show the list or the detail; this returns from the detail to the list. */
  onBack: () => void
  backLabel?: string
}

/** A list beside the selected item's detail, inside a `Page fill width="full"`. Below `md` it shows the detail alone, or the list above the placeholder. */
export const SplitView = ({ listTitle, list, detail, placeholder, onBack, backLabel = 'Back to list' }: SplitViewProps) => (
  <div className={cx('h-full flex md:flex-row bg-white rounded-xl border border-slate-200 shadow-sm',
    detail ? 'overflow-hidden' : 'flex-col overflow-y-auto md:overflow-hidden')}>
    <div className={cx('w-full shrink-0 md:w-80 md:border-r border-slate-200 flex-col', detail ? 'hidden md:flex' : 'flex border-b md:border-b-0')}>
      {listTitle && <PaneHeader title={listTitle} />}
      <div className="flex-1 md:overflow-y-auto">{list}</div>
    </div>
    <div className={cx('flex-1 min-w-0 flex flex-col', detail ? 'overflow-hidden' : 'md:overflow-hidden')}>
      {detail ? (
        <>
          <button type="button" onClick={onBack} className="md:hidden text-left px-4 py-2 text-sm text-slate-600 hover:text-primary border-b border-slate-200">
            <span aria-hidden="true">← </span>{backLabel}
          </button>
          {detail}
        </>
      ) : (
        <div className="flex-1 md:overflow-y-auto">
          {typeof placeholder === 'string' || !placeholder
            ? <div className="h-full flex items-center justify-center p-6 text-slate-600">{placeholder}</div>
            : placeholder}
        </div>
      )}
    </div>
  </div>
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
// Small buttons are in-row actions; on phones they grow to a tappable height.
const BUTTON_SIZES = { md: 'py-2 px-4 text-sm', sm: 'py-1 px-2.5 text-xs max-sm:min-h-10 max-sm:px-3', icon: 'w-9 h-9 text-lg' }
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

const CONTROL = 'block py-2 px-3 bg-white border border-slate-300 rounded-lg text-sm text-slate-800 placeholder:text-slate-500 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:bg-slate-50 disabled:text-slate-500'
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

type FieldControlProps = { id?: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean }

/**
 * A labelled control. When exactly one child element is rendered (conditional children that render
 * nothing don't count), it gets an id wired to the label, and the hint and error describe it.
 */
export const Field = ({ label, hint, error, className, children, ...props }: FieldProps) => {
  const generated = useId()
  const elements = Children.toArray(children).filter(isValidElement)
  const control = elements.length === 1 && elements[0].type !== Fragment ? (elements[0] as ReactElement<FieldControlProps>) : null
  const id = control?.props.id ?? generated
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const wire = (child: ReactNode) => !isValidElement<FieldControlProps>(child) ? child : cloneElement(child, {
    id,
    'aria-describedby': cx(child.props['aria-describedby'], hintId, errorId) || undefined,
    'aria-invalid': child.props['aria-invalid'] ?? (error ? true : undefined),
  })
  return (
    <div className={cx('space-y-1', className)} {...props}>
      {label && <Label htmlFor={control ? id : undefined}>{label}</Label>}
      {control ? Children.map(children, wire) : children}
      {hint && <Hint id={hintId}>{hint}</Hint>}
      {error && <p id={errorId} className="text-xs text-red-700">{error}</p>}
    </div>
  )
}

export const FieldRow = ({ className, ...props }: ComponentProps<'div'>) => (
  <div className={cx('grid grid-cols-1 md:grid-cols-2 gap-4', className)} {...props} />
)

export const FormActions = ({ className, ...props }: ComponentProps<'div'>) => (
  <div className={cx('flex flex-wrap justify-end gap-2 pt-2', className)} {...props} />
)

// The whole label is the hit area, padded to a tappable height on phones.
export const Checkbox = ({ label, className, ...props }: ComponentProps<'input'> & { label: ReactNode }) => (
  <label className={cx('inline-flex items-center gap-2 text-sm text-slate-700 cursor-pointer max-sm:min-h-10', className)}>
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

/**
 * Runs actions on a loaded page (sync, toggle, delete) and keeps the outcome for a `Notice`,
 * so a failure is never silent. `run` resolves to whether the action succeeded.
 */
export const useActionNotice = () => {
  const [outcome, setOutcome] = useState<{ tone: 'error' | 'success'; message: string } | null>(null)
  const run = useCallback(async (action: () => Promise<unknown>, success?: string) => {
    setOutcome(null)
    try {
      await action()
    } catch (e) {
      setOutcome({ tone: 'error', message: e instanceof Error ? e.message : String(e) })
      return false
    }
    if (success) setOutcome({ tone: 'success', message: success })
    return true
  }, [])
  const notice = outcome && <Notice tone={outcome.tone} onDismiss={() => setOutcome(null)}>{outcome.message}</Notice>
  return { run, notice }
}

interface EmptyStateProps {
  message: ReactNode
  actionLabel?: string
  onAction?: () => void
  /** An action that isn't a button, such as a ButtonLink. */
  action?: ReactNode
}

export const EmptyState = ({ message, actionLabel, onAction, action }: EmptyStateProps) => (
  <div className="rounded-xl border border-dashed border-slate-300 py-10 px-4 text-center text-slate-600">
    <div>{message}</div>
    {actionLabel && onAction && <Button variant="primary" className="mt-4" onClick={onAction}>{actionLabel}</Button>}
    {action && <div className="mt-4">{action}</div>}
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
          className={cx('px-3 py-1 rounded-full text-xs font-medium border transition-colors max-sm:min-h-10',
            o.value === value ? 'bg-primary border-primary text-white' : 'bg-white border-slate-300 text-slate-700 hover:bg-slate-50')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ---- Dialogs ----

function useEscape(onEscape?: () => void) {
  useEffect(() => {
    if (!onEscape) return
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
  /** Omit to make the dialog closable only by its own content, e.g. a one-time secret's Done button. */
  onClose?: () => void
  children: ReactNode
  size?: keyof typeof MODAL_SIZES
  className?: string
}

const FOCUSABLE = 'a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'

// Focus `initial` (else the dialog's first control) on open, keep Tab inside the dialog, and restore focus on close.
function useDialogFocus(dialogRef: RefObject<HTMLElement | null>, initial: () => HTMLElement | null | undefined) {
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null
    const dialog = dialogRef.current
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])
    ;(initial() ?? focusable()[0])?.focus()
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
}

/** Closes only via × or Escape: a stray backdrop click must not discard a half-filled form. */
export const Modal = ({ title, onClose, children, size = 'lg', className }: ModalProps) => {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  useEscape(onClose)
  useDialogFocus(dialogRef, () => bodyRef.current?.querySelector<HTMLElement>(FOCUSABLE))

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId}
        className={cx('bg-white rounded-xl shadow-xl w-full max-h-[85vh] overflow-auto', MODAL_SIZES[size], className)}>
        <div className="flex items-center justify-between p-4 border-b border-slate-100">
          <h2 id={titleId} className="text-lg font-semibold text-slate-800">{title}</h2>
          {onClose && <button aria-label="Close" className="text-slate-500 hover:text-slate-700 text-2xl leading-none" onClick={onClose}>&times;</button>}
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

interface ConfirmButtonProps extends Omit<ButtonProps, 'onClick'> {
  /** The question in the dialog. */
  confirm: string
  onConfirm: () => void | Promise<void>
  confirmLabel?: string
}

/** A button that asks before acting: the one way to confirm deletes and other destructive actions. */
export const ConfirmButton = ({ confirm, onConfirm, confirmLabel, variant = 'danger', size = 'sm', children = 'Delete', ...props }: ConfirmButtonProps) => {
  const [asking, setAsking] = useState(false)
  return (
    <>
      <Button variant={variant} size={size} onClick={() => setAsking(true)} {...props}>{children}</Button>
      {asking && (
        <ConfirmDialog message={confirm} confirmLabel={confirmLabel ?? (typeof children === 'string' ? children : 'Confirm')}
          onCancel={() => setAsking(false)}
          onConfirm={async () => { setAsking(false); await onConfirm() }} />
      )}
    </>
  )
}

export const ConfirmDialog = ({ message, onConfirm, onCancel, title, confirmLabel = 'Confirm' }: ConfirmDialogProps) => {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  useEscape(onCancel)
  // Cancel is the safer default focus for destructive confirmations
  useDialogFocus(dialogRef, () => cancelRef.current)

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} className="bg-white rounded-xl shadow-xl p-6 max-w-sm w-full">
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
