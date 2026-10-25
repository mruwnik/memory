import { useEffect, useState } from 'react'
import { useSSHKeys, type SSHKey } from '../../hooks/useSSHKeys'
import { Field, Select, Notice } from '@/components/shared/ui'

export default function SSHKeySelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { list } = useSSHKeys()
  const [keys, setKeys] = useState<SSHKey[]>([])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    list().then(items => { if (active) setKeys(items) }).catch(e => { if (active) setError(e instanceof Error ? e.message : 'Could not load SSH keys') })
    return () => { active = false }
  }, [list])
  return <>
    {error && <Notice tone="error">{error}</Notice>}
    <Field label="SSH key" hint={<>Manage keys in <a href="/ui/ssh-keys">System operations → SSH keys</a>. Changes apply to new containers.</>}>
      <Select value={value} onChange={e => onChange(e.target.value)}>
        <option value="">Use my default{keys.find(key => key.is_default) ? ` (${keys.find(key => key.is_default)?.name})` : ''}</option>
        <option value="none">No SSH key</option>
        {keys.map(key => <option key={key.id} value={key.id}>{key.name}{key.user_id === null ? ' (instance)' : ''}</option>)}
      </Select>
    </Field>
  </>
}
