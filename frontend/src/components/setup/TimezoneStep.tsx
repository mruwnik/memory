import { useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { useUsers } from '@/hooks/useUsers'
import { getBrowserTimezone, timezoneOptions } from '@/utils/timezones'
import { Button, Combobox, Field, FormActions, Notice, useSubmit } from '@/components/shared/ui'

/** `actions` sit beside the submit button, so the last step has one row of buttons. */
export const TimezoneStep = ({ userId, onDone, actions }: { userId: number; onDone: () => void; actions: ReactNode }) => {
  const { updateUser } = useUsers()
  const [timezone, setTimezone] = useState(getBrowserTimezone)
  const options = useMemo(timezoneOptions, [])
  const { busy, error, submit } = useSubmit()

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (await submit(() => updateUser(userId, { timezone }))) onDone()
  }

  return (
    <form onSubmit={save} className="space-y-4">
      <Field label="Timezone" hint="Scheduled tasks and agent schedules run on this clock. Pick one or type any IANA name, such as Asia/Kolkata.">
        <Combobox value={timezone} onChange={setTimezone} options={options} allowCustom />
      </Field>
      {error && <Notice tone="error">{error}</Notice>}
      <FormActions>
        {actions}
        <Button type="submit" variant="primary" disabled={busy || !timezone}>{busy ? 'Saving...' : 'Save and finish'}</Button>
      </FormActions>
    </form>
  )
}
