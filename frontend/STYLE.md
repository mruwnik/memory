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

- `loading` and `error` are for the initial load only. Errors from actions on a loaded page go in a `<Notice>`.
- `fill` makes the content fill the viewport below the header, for split panes that scroll
  internally (Notes, Reports, Claude sessions). Use it with `width="full"`.
- Use `<Section title actions? description?>` for titled blocks inside a page, and `<Card title? actions?>`
  for a white panel. Pass `flush` to a Card whose content runs edge to edge.

## Primitives

| Need | Use |
|------|-----|
| Button / link that looks like one | `Button`, `ButtonLink` with `variant`: `primary` (one per area), `secondary` (default), `danger` (destructive, outlined), `dangerSolid` (confirm dialogs), `ghost`, `link`; `size`: `md`, `sm` (in-row actions), `icon` |
| Non-button element with button looks (file-upload label) | `className={buttonClass(variant, size)}` |
| Lists and tables | `List` + `ListItem`; `Table` (plain `<th>`/`<td>`, the wrapper styles them) |
| Form fields | `Field label hint error` around one `Input` / `Select` / `Textarea` (it wires the label to the control); `FieldRow` for two columns; `FormActions` for the submit row; `Checkbox label` |
| Picking several values from a list | `MultiSelect options value onChange` (searchable, picked values as removable chips; `creatable` for free-form tags). Options are strings or `{ value, label?, description? }`; it takes `id`, so `Field` wires its label |
| Status | `Badge tone` (`neutral`, `info`, `success`, `warning`, `danger`, `primary`) |
| Messages | `Notice tone` (`error`, `warning`, `info`, `success`), with optional `onRetry` / `onDismiss` |
| Empty / loading / failed | `EmptyState`, `LoadingState`, `ErrorState` |
| Switching views or filters | `Tabs` (views), `ToggleGroup` (filters, ranges, modes) |
| Dialogs | `Modal title onClose size` (focus, Tab trap, Escape), `ConfirmDialog` for yes/no questions |

## Tokens

Colours live in `src/index.css` (`@theme`): use `primary`, `primary-dark`, `success`, `warning` and `danger`,
plus Tailwind's `slate` greys. Spacing, radii and type sizes are Tailwind's default scale.
Muted text is `text-slate-500` or darker (`slate-400` fails contrast on white). Plain Tailwind utilities are
fine for layout specific to one view (grids, chart sizes, calendar cells), but not for anything a primitive covers.
