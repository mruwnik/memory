import { useState } from 'react'
import type { Person, PersonCreate, PersonUpdate, SlackContact } from '../../hooks/usePeople'
import { Badge, Button, Input, List, Modal, MultiSelect, Notice, Row, Textarea, Field, FieldRow, Fieldset, FormActions, Hint, requiredLabel } from '@/components/shared/ui'

interface PersonFormModalProps {
  title: string
  initialData?: Person
  onSubmit: (data: PersonCreate | PersonUpdate) => Promise<void>
  onCancel: () => void
  loading: boolean
  error: string | null
  submitLabel: string
  isEdit?: boolean
}

type ContactKey = 'email' | 'phone' | 'github' | 'discord' | 'twitter' | 'website'

// Merged people can hold several values for one contact field
type ContactValue = string | string[]

interface FormData extends Record<ContactKey, ContactValue> {
  identifier: string
  display_name: string
  aliases: string
  tags: string
  notes: string
  slackTeam: string
  slackUser: string
}

interface ContactField {
  key: ContactKey
  label: string
  type: string
  placeholder: string
  hint?: string
  pattern?: string
  inputMode?: 'numeric'
}

const CONTACT_FIELDS: ContactField[] = [
  { key: 'email', label: 'Email', type: 'email', placeholder: 'alice@example.com' },
  { key: 'phone', label: 'Phone', type: 'tel', placeholder: '+1 555-1234' },
  { key: 'github', label: 'GitHub', type: 'text', placeholder: '@alicechen' },
  {
    key: 'discord', label: 'Discord user id', type: 'text', placeholder: '123456789012345678', pattern: '[0-9]{1,19}', inputMode: 'numeric',
    hint: 'The number from "Copy User ID" in Discord, not the username: usernames can pass to someone else.',
  },
  { key: 'twitter', label: 'Twitter/X', type: 'text', placeholder: '@alice_c' },
  { key: 'website', label: 'Website', type: 'url', placeholder: 'https://alice.dev' },
]

// Discord ids can be stored as numbers; the form edits text.
const contactValue = (raw: unknown): ContactValue =>
  Array.isArray(raw) ? raw.map(String) : typeof raw === 'string' || typeof raw === 'number' ? String(raw) : ''

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/** contact_info.slack as team id -> account rows; null when it isn't one team map (a merged person's list), which the form can't edit. */
const slackTeams = (raw: unknown): [string, SlackContact][] | null => {
  if (raw === undefined) return []
  if (!isRecord(raw)) return null
  return Object.entries(raw).flatMap(([team, info]) =>
    isRecord(info) ? [[team, { ...info, user_id: contactValue(info.user_id).toString() } as SlackContact]] : [])
}

const PersonFormModal = ({
  title,
  initialData,
  onSubmit,
  onCancel,
  loading,
  error,
  submitLabel,
  isEdit = false,
}: PersonFormModalProps) => {
  const initialForm: FormData = {
    identifier: initialData?.identifier || '',
    display_name: initialData?.display_name || '',
    aliases: initialData?.aliases?.join(', ') || '',
    tags: initialData?.tags?.join(', ') || '',
    notes: '',
    ...Object.fromEntries(CONTACT_FIELDS.map(({ key }) => [key, contactValue(initialData?.contact_info?.[key])])) as Record<ContactKey, ContactValue>,
    slackTeam: '',
    slackUser: '',
  }
  const [form, setForm] = useState<FormData>(initialForm)
  const slack = slackTeams(initialData?.contact_info?.slack)
  const [slackRemoved, setSlackRemoved] = useState<string[]>([])
  const slackPartial = !form.slackTeam.trim() !== !form.slackUser.trim()
  const slackAdded = form.slackTeam.trim() && form.slackUser.trim()
    ? { [form.slackTeam.trim()]: { user_id: form.slackUser.trim() } } : {}

  const generateIdentifier = (name: string): string => {
    return name
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '') // Remove diacritics
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, '')
      .replace(/\s+/g, '_')
      .substring(0, 50)
  }

  const handleDisplayNameChange = (value: string) => {
    const updates: Partial<FormData> = { display_name: value }
    // Auto-generate identifier if creating new and identifier is empty or was auto-generated
    if (!isEdit && (!form.identifier || form.identifier === generateIdentifier(form.display_name))) {
      updates.identifier = generateIdentifier(value)
    }
    setForm({ ...form, ...updates })
  }

  const parseCommaSeparated = (value: string): string[] => {
    return value
      .split(',')
      .map(s => s.trim())
      .filter(s => s.length > 0)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (slackPartial) return
    // Teams merge on the server, so only added and removed teams are sent. An added
    // team clears the names a replaced user id had, which would belong to someone else.
    const slackChanges: Record<string, SlackContact | null> = {
      ...Object.fromEntries(slackRemoved.map(team => [team, null])),
      ...Object.fromEntries(Object.entries(slackAdded).map(([team, info]) => [team, { ...info, username: null, display_name: null }])),
    }

    if (isEdit) {
      // The server only removes what is sent as null, so cleared fields and dropped aliases go as null.
      // Unchanged fields aren't sent: a merged person's list value would come back as one joined string.
      const contact_info = Object.fromEntries(
        CONTACT_FIELDS.flatMap(({ key }): [string, NonNullable<PersonUpdate['contact_info']>[string]][] => {
          const [before, after] = [initialForm[key], form[key]]
          if (Array.isArray(before) && Array.isArray(after)) {
            // Lists are additive on the server: an entry is added with true and removed with null.
            const changes = [
              ...after.filter(v => !before.includes(v)).map(v => [v, true] as const),
              ...before.filter(v => !after.includes(v)).map(v => [v, null] as const),
            ]
            return changes.length ? [[key, Object.fromEntries(changes)]] : []
          }
          return before === after ? [] : [[key, (after as string) || null]]
        }),
      )
      const aliases = parseCommaSeparated(form.aliases)
      const data: PersonUpdate = {
        display_name: form.display_name || undefined,
        aliases: Object.fromEntries([
          ...aliases.map(alias => [alias, true] as const),
          ...(initialData?.aliases ?? []).filter(alias => !aliases.includes(alias)).map(alias => [alias, null] as const),
        ]),
        tags: parseCommaSeparated(form.tags),
        content: form.notes || undefined,
        contact_info: Object.keys(slackChanges).length ? { ...contact_info, slack: slackChanges } : contact_info,
      }
      await onSubmit(data)
    } else {
      const contact_info: NonNullable<PersonCreate['contact_info']> = Object.fromEntries(
        CONTACT_FIELDS.filter(({ key }) => form[key]).map(({ key }) => [key, form[key] as string]))
      if (Object.keys(slackAdded).length) contact_info.slack = slackAdded
      const data: PersonCreate = {
        identifier: form.identifier,
        display_name: form.display_name,
        aliases: parseCommaSeparated(form.aliases),
        tags: parseCommaSeparated(form.tags),
        content: form.notes || undefined,
        contact_info: Object.keys(contact_info).length > 0 ? contact_info : undefined,
      }
      await onSubmit(data)
    }
  }

  const identifierHint = isEdit ? undefined
    : form.display_name && !form.identifier ? "This name has no letters we can build an identifier from: type an identifier (lowercase letters, numbers, underscores)."
    : 'Auto-generated from the name; lowercase, no spaces. Cannot be changed later.'

  return (
    <Modal title={title} onClose={onCancel}>
      {error && (
          <Notice tone="error" role="alert" className="mb-4">
            {error}
          </Notice>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label={requiredLabel('Display name')}>
            <Input
              id="person-display-name"
              type="text"
              value={form.display_name}
              onChange={(e) => handleDisplayNameChange(e.target.value)}
              required
              placeholder="e.g., Alice Chen"
            />
          </Field>

          <Field label={requiredLabel('Identifier')} hint={identifierHint}>
            <Input
              id="person-identifier"
              type="text"
              value={form.identifier}
              onChange={(e) => setForm({ ...form, identifier: e.target.value })}
              required
              disabled={isEdit}
              placeholder="e.g., alice_chen"
              pattern="[a-z0-9_]+"
              title="Only lowercase letters, numbers, and underscores"
              className="disabled:bg-slate-50"
            />
          </Field>

          <Field label="Aliases" hint="Comma-separated alternative names or handles">
            <Input
              id="person-aliases"
              type="text"
              value={form.aliases}
              onChange={(e) => setForm({ ...form, aliases: e.target.value })}
              placeholder="e.g., @alice_c, alice.chen@work.com"
            />
          </Field>

          <Field label="Tags" hint="Comma-separated tags for categorization">
            <Input
              id="person-tags"
              type="text"
              value={form.tags}
              onChange={(e) => setForm({ ...form, tags: e.target.value })}
              placeholder="e.g., work, friend, engineering"
            />
          </Field>

          <Fieldset legend="Contact information" className="pt-2 space-y-3">
            <FieldRow>
              {CONTACT_FIELDS.map(({ key, label, type, placeholder, hint, pattern, inputMode }) => {
                const value = form[key]
                return (
                  <Field key={key} label={label} hint={hint}>
                    {Array.isArray(value) ? (
                      <MultiSelect
                        id={`person-${key}`}
                        options={initialForm[key] as string[]}
                        value={value}
                        onChange={(entries) => setForm({ ...form, [key]: entries })}
                        placeholder={placeholder}
                        creatable
                      />
                    ) : (
                      <Input
                        id={`person-${key}`}
                        type={type}
                        value={value}
                        onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                        placeholder={placeholder}
                        pattern={pattern}
                        inputMode={inputMode}
                        title={pattern ? 'Digits only' : undefined}
                      />
                    )}
                  </Field>
                )
              })}
            </FieldRow>
          </Fieldset>

          <Fieldset legend="Slack" className="space-y-3">
            {slack === null ? (
              <Hint>This person's Slack accounts came from several merged records and are stored as a list, which this form can't edit. Saving keeps them as they are.</Hint>
            ) : (
              <>
                {slack.length > 0 && (
                  <List flush aria-label="Slack accounts">
                    {slack.map(([team, info]) => {
                      const removed = slackRemoved.includes(team)
                      return (
                        <Row key={team} compact muted={removed} title={info.display_name || info.username || info.user_id}
                          badges={removed && <Badge tone="warning">removed on save</Badge>}
                          meta={[`team ${team}`, `user ${info.user_id}`]}
                          actions={
                            <Button size="sm" onClick={() => setSlackRemoved(removed ? slackRemoved.filter(t => t !== team) : [...slackRemoved, team])}>
                              {removed ? 'Undo' : 'Remove'}
                            </Button>
                          } />
                      )
                    })}
                  </List>
                )}
                <FieldRow>
                  <Field label="Slack team id" error={slackPartial && !form.slackTeam.trim() ? 'Enter the team id too' : null}>
                    <Input id="person-slack-team" value={form.slackTeam} placeholder="T01ABCDEF"
                      onChange={(e) => setForm({ ...form, slackTeam: e.target.value })} />
                  </Field>
                  <Field label="Slack user id" error={slackPartial && !form.slackUser.trim() ? 'Enter the user id too' : null}>
                    <Input id="person-slack-user" value={form.slackUser} placeholder="U07ABCDEF"
                      onChange={(e) => setForm({ ...form, slackUser: e.target.value })} />
                  </Field>
                </FieldRow>
                <Hint>
                  Add an account by its ids: the user id from "Copy member ID" on their Slack profile, the team id from
                  the browser address (app.slack.com/client/T…).
                  {slack.some(([team]) => team === form.slackTeam.trim()) && ' This team is already listed: saving replaces its user id.'}
                </Hint>
              </>
            )}
          </Fieldset>

          <Field label="Add a note">
            <Textarea
              id="person-notes"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              rows={4}
              placeholder="Saved as a tidbit. Existing tidbits are shown on the person card."
            />
          </Field>

          <FormActions>
            <Button type="button" onClick={onCancel}>Cancel</Button>
            <Button variant="primary"
              type="submit"
              disabled={loading || !form.display_name || !form.identifier || slackPartial}
            >
              {loading ? 'Saving...' : submitLabel}
            </Button>
          </FormActions>
        </form>
    </Modal>
  )
}

export default PersonFormModal
