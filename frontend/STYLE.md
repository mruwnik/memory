# Frontend style

Every view is built from one page template and a small set of primitives, all exported from
`@/components/shared/ui`. Don't write your own page container, header, button, input or banner
class strings. If a primitive is missing something, extend it there so every page gets the change.

## Page template

Signed-in routes render inside `AppShell`: a top bar (home link, user menu) and a section sidebar.
The sidebar lists every section from `shared/nav.ts`, grouped and filtered by the user's scopes, and
highlights the current one; below the `lg` breakpoint it becomes a drawer opened from the top bar.
The Dashboard renders the same list as tiles with descriptions, so a new section is added once, in
`nav.ts` (plus its route in `App.tsx`, with the same scope).

Each route component returns exactly one `<Page>`:

```tsx
<Page
  title="MCP servers"                      // ReactNode: a status badge can sit next to the text
  back={{ to: '/ui/agents', label: 'Agents' }}  // only for sub-pages; the top bar already links home
  description="Remote MCP servers your agents can call."
  actions={<Button variant="primary">Add server</Button>}   // rendered on a row under the title
  width="default"                          // narrow | default | wide | full
  loading={items === null && 'Loading servers...'}
  error={loadError} onRetry={load}         // blocking load failure: replaces the content
>
  ...
</Page>
```

- `loading` and `error` are for the initial load only. Actions on a loaded page (sync, toggle, delete) run through
  `useActionNotice`, whose `notice` shows the failure (with the server's reason) or a short success; never swallow an error.
- `fill` makes the content fill the viewport below the header, for split panes that scroll
  internally (Notes, Reports, Claude sessions). Use it with `width="full"`.
- Use `<Section title actions? description?>` for titled blocks inside a page, and `<Card title? description? actions?>`
  for a white panel. Pass `flush` to a Card whose content runs edge to edge.

## Layouts: which one for which page

Inside the `Page`, every view is one of five layouts. Pick by what the page is for, not by what the
data looks like.

| The page… | Layout | Examples |
|-----------|--------|----------|
| lists things the user filters and acts on | Collection | Jobs, Check, Scheduled tasks, People, Teams, Projects, Polls, Users, Sources tabs |
| shows one thing | Detail | Agent run, MCP server, Poll results |
| browses a list while reading one item beside it | Master–detail | Notes, Reports, Claude sessions |
| edits settings or creates one thing | Form | User settings, Create poll, Forums sync |
| watches numbers over time | Dashboard | Metrics, Telemetry, Celery |

Specialised views (the calendar grid, poll availability grid, log viewer, terminal) sit inside these as a
`Card`; they are the only place for one-off layout.

### Collection

Order, top to bottom: `Page` (create action in `actions`), `Toolbar`, `Notice`s, then the
results: `List` of `Row`s, or `Table` when users compare values across columns. A collection that
lives inside a page section (a Sources tab) uses `Section title actions` in place of the page header.
Show `LoadingState` / `EmptyState` in place of the list, and `Pager` under it for paged APIs.

```tsx
<Page title="Jobs" description="2 failed · 98 complete" actions={<Button variant="primary">New job</Button>}>
  <Toolbar selected={picked.size} onClearSelection={clear} actions={<RefreshButton onClick={load} />}>
    <Input type="search" aria-label="Search jobs" className="sm:w-64" ... />
    <ToggleGroup label="Status" options={FILTERS} value={status} onChange={setStatus} />
  </Toolbar>
  <List>
    {jobs.map(job => (
      <Row key={job.id} title={job.name} to={`/ui/jobs/${job.id}`}
        badges={<Badge tone="danger">failed</Badge>}
        meta={[job.external_id, timeAgo(job.created_at), `${job.attempts} attempts`]}
        actions={<><Button size="sm">Retry</Button><ConfirmButton confirm="Delete this job?" onConfirm={...} /></>}>
        {job.error && <RowError>{job.error}</RowError>}
      </Row>
    ))}
  </List>
</Page>
```

- **Row anatomy** is the same everywhere: title, then badges (status, type, tags), then one meta line of
  short facts joined by `·`, trailing `actions` (they wrap below on phones), then an optional body
  (description, `RowError`, nested `SubList`). `leading` holds a selection checkbox or avatar.
- **Clickable rows**: `to` (navigates) or `onSelect` (selects or expands, with `selected` / `expanded`).
  The title becomes the link or button and its hit area covers the row header, so actions stay separate
  focus targets. Don't put `onClick` on an `li`.
- **Status** is a `Badge` with a tone from a per-file `Record<status, Tone>`. No coloured borders or bespoke
  pill classes; `muted` greys an archived or inactive row's title.
- **Filters** go in the `Toolbar` (left); **refresh and bulk actions** go in its `actions` (right). With
  `selected`, the toolbar shows "N selected · Clear" before the bulk actions.
- **Create and edit** open a `Modal`; only a single-field rename (a task title) edits inline.
- **Destructive actions** use `ConfirmButton`, never `window.confirm`/`alert` or an inline "Delete? Yes/No".
- **On/off state** (an active source, an enabled schedule) is a static `Badge`; changing it is a row action ("Pause"/"Resume"),
  not a clickable badge.
- **Nested collections** (a GitHub account's repos, a bot's servers, run history) are a `SubList` in the
  row body; make its `title` an `ExpandToggle` when it loads lazily.
- **Card grids** are only for media (Photos) and the Dashboard home tiles.

### Detail

`Page back title actions`, then a `DetailList` of facts, then `Section`s for related collections.

```tsx
<Page back={{ to: '/ui/agents', label: 'Agents' }} title={server.name} actions={<Button>Edit</Button>}>
  <DetailList items={[['URL', server.url], ['Status', <Badge tone="success">connected</Badge>], ['Tools', tools.length]]} />
  <Section title="Tools"><List>...</List></Section>
  <Disclosure summary="Raw response"><pre>...</pre></Disclosure>
</Page>
```

`DetailList` drops entries whose value is empty, so pass optional facts unconditionally. Secondary or
bulky detail (commands, raw JSON, embedded tools) goes in a `Disclosure`.

### Master–detail

`Page fill width="full"` around a `SplitView`. The list pane is a flush `List` of `Row`s with `onSelect`
and `selected`; the detail starts with a `PaneHeader` and scrolls on its own. Below `md` it shows the
detail alone, with a back button to the list, or the list with the placeholder under it. Keep the selection in the URL
(`?id=` or a path segment) so it survives reloads.

```tsx
<Page title="Reports" width="full" fill actions={<Button variant="primary">New Report</Button>}>
  <SplitView
    listTitle={`${reports.length} reports`}
    list={<List flush>{reports.map(r => <Row key={r.id} title={r.title} onSelect={() => select(r)} selected={r.id === id} />)}</List>}
    detail={report && <><PaneHeader title={report.title} actions={<Button size="sm">Edit</Button>} /><iframe ... /></>}
    placeholder="Select a report to view"
    onBack={() => select(null)}
  />
</Page>
```

### Form

`Page width="narrow"`, one `Card title description?` per group of settings, `Field`s inside (`FieldRow` for
pairs, which stack on phones), `FormActions` at the bottom of each card. Settings that are actions rather
than values (change password, regenerate key) are `Row`s in a flush `List` inside the card.

```tsx
<Page title="User Settings" width="narrow">
  <Card title="Profile">
    <Field label="Name"><Input value={name} onChange={...} /></Field>
    <FormActions><Button variant="primary">Save Changes</Button></FormActions>
  </Card>
  <Card title="Security" flush>
    <List flush><Row title="API Key" meta={['No API key configured']} actions={<Button size="sm">Generate</Button>} /></List>
  </Card>
</Page>
```

### Dashboard

`Toolbar` with the time range and refresh, a `StatGrid` of `Stat`s for headline numbers, then a
`CardGrid` of chart and recent-activity `Card`s (`className="lg:col-span-2"` for a wide one).

```tsx
<Toolbar actions={<RefreshButton onClick={load} />}><ToggleGroup label="Time range" ... /></Toolbar>
<StatGrid>
  <Stat label="Success Rate" value="97%" tone="success" />
  <Stat label="Memory" value="62%" tone="warning" hint="5.1 GB / 8 GB" />
</StatGrid>
<CardGrid>
  <Card title="System Health" className="lg:col-span-2"><Chart /></Card>
  <Card title="Recent Calls"><Table>...</Table></Card>
</CardGrid>
```

A page with only a couple of counts (Tasks, Check) puts them in the `Page` `description` instead of tiles.

## Primitives

| Need | Use |
|------|-----|
| Button / link that looks like one | `Button`, `ButtonLink` with `variant`: `primary` (one per area), `secondary` (default), `danger` (destructive, outlined), `dangerSolid` (confirm dialogs), `ghost`, `link`; `size`: `md`, `sm` (in-row actions), `icon` |
| Non-button element with button looks (file-upload label) | `className={buttonClass(variant, size)}` |
| Lists and tables | `List` + `Row` (see Collection); `SubList` inside a row; `Table` (plain `<th>`/`<td>`, the wrapper styles them) |
| Toolbars and paging | `Toolbar`, `RefreshButton`, `Pager` |
| Facts and numbers | `DetailList`, `StatGrid` + `Stat` |
| Collapsing | `Disclosure` (secondary blocks), `ExpandToggle` (a row's nested list) |
| Split panes | `SplitView`, `PaneHeader` |
| Form fields | `Field label hint error` around one `Input` / `Select` / `Textarea` / `Combobox` / `MultiSelect` (it wires the label to the one control its children render, and the hint and error to its `aria-describedby`); `FieldRow` for two columns; `FormActions` for the submit row; `Checkbox label` |
| Picking several values from a list | `MultiSelect options value onChange` (searchable, picked values as removable chips; `creatable` for free-form tags). Options are strings or `{ value, label?, description? }`; it takes `id`, so `Field` wires its label |
| Status | `Badge tone` (`neutral`, `info`, `success`, `warning`, `danger`, `primary`) |
| Messages | `Notice tone` (`error`, `warning`, `info`, `success`), with optional `onRetry` / `onDismiss`; `useActionNotice` for the outcome of actions. Hooks throw `apiError(response, fallback)`, which carries the server's `detail` |
| Empty / loading / failed | `EmptyState` (`actionLabel`+`onAction`, or `action` for a link), `LoadingState`, `ErrorState`; `RowError` for one item's failure |
| Switching views or filters | `Tabs` (views), `ToggleGroup` (filters, ranges, modes) |
| Dialogs | `Modal title onClose size` (focus, Tab trap, closes on × or Escape, never on a backdrop click); `ConfirmButton confirm onConfirm` for destructive actions; `ConfirmDialog` when the question opens from somewhere else |

## Tokens

Colours live in `src/index.css` (`@theme`): use `primary`, `primary-dark`, `success`, `warning` and `danger`,
plus Tailwind's `slate` greys. Spacing, radii and type sizes are Tailwind's default scale.
Muted text is `text-slate-500` or darker (`slate-400` fails contrast on white). Plain Tailwind utilities are
fine for layout specific to one view (grids, chart sizes, calendar cells), but not for anything a primitive covers.
